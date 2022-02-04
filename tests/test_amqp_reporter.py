from email.parser import BytesParser as EmailParser
from email.policy import default as default_policy
from threading import Thread
import os
from pathlib import Path
from queue import Queue, Empty
from subprocess import Popen, PIPE
import ssl
from typing import BinaryIO, NamedTuple, List, Tuple

from aiosmtpd.controller import Controller as SMTPController
from aiosmtpd import smtp
from elasticsearch6 import Elasticsearch
import kombu
import urllib3

import pytest

from ensembl.production.reporting.config import config
from ensembl.production.reporting.amqp_reporter import validate_payload, compose_email
from ensembl.production.reporting.amqp_reporter import AMQP_URI


SMTP_TEST_SERVER_HOST = "127.0.0.1"
SMTP_TEST_SERVER_PORT = 10025

AMQP_MANAGEMENT_PORT = 15672

THIS_DIR = Path(__file__).resolve().parent

class HostPort(NamedTuple):
    host: str
    port: int


class SMTPMessage(NamedTuple):
    remote_host: HostPort
    sender: str
    receivers: List[str]
    message: str


def queue_consumer(queue: Queue):
    while True:
        try:
            yield queue.get(timeout=5)
        except Empty:
            break


def wait_for(url: str, retries: int = 8, backoff: float = 0.2):
    retry = urllib3.Retry(
        total=retries,
        backoff_factor=backoff,
        status_forcelist=[404, 500, 502, 503, 504],
    )
    manager = urllib3.PoolManager(retries=retry)
    manager.request("GET", url)


@pytest.fixture(scope="session")
def valid_email_message():
    message = {
        "subject": "Test Email",
        "to": ["receiver1@email.org", "receiver2@email.org"],
        "content": "Hello!",
    }
    return message


@pytest.fixture
def amqp_publish():
    wait_for(f"http://{config.amqp_host}:{AMQP_MANAGEMENT_PORT}/")
    connection = kombu.Connection(AMQP_URI)
    queue = connection.SimpleQueue("test_queue")

    def publisher(message: dict) -> None:
        queue.put(message, serializer="json")

    try:
        yield publisher
    finally:
        queue.clear()
        queue.close()


@pytest.fixture
def elastic_search():
    wait_for(f"http://{config.es_host}:{config.es_port}/")
    es = Elasticsearch([{"host": config.es_host, "port": config.es_port}])

    def search(body: dict) -> None:
        es.indices.flush()
        es.indices.refresh()
        return es.search(index="test", body=body)

    try:
        yield search
    finally:
        if es.indices.exists("test"):
            es.indices.delete("test")


@pytest.fixture
def smtp_messages():
    message_q: Queue = Queue()
    ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ssl_context.load_cert_chain(THIS_DIR/"test_cert.pem", THIS_DIR/"test_key.pem")

    class SMTPHandler:
        async def handle_DATA(self, _server, session, envelope):
            peer = session.peer
            mail_from = envelope.mail_from
            rcpt_tos = envelope.rcpt_tos
            data = envelope.content  # type: bytes
            msg = SMTPMessage(peer, mail_from, rcpt_tos, data)
            message_q.put(msg)
            return "250 OK"

    def smtp_authenticator(_server, _session, _envelope, mechanism, auth_data):
        auth_failed = smtp.AuthResult(success=False, handled=False)
        if mechanism != "LOGIN":
            return auth_failed
        username = auth_data.login.decode("utf-8")
        password = auth_data.password.decode("utf-8")
        if username != config.smtp_user or password != config.smtp_pass:
            return auth_failed
        return smtp.AuthResult(success=True)

    controller = SMTPController(
        SMTPHandler(),
        hostname=SMTP_TEST_SERVER_HOST,
        port=SMTP_TEST_SERVER_PORT,
        auth_required=True,
        authenticator=smtp_authenticator,
        tls_context=ssl_context
    )
    controller.start()
    try:
        yield queue_consumer(message_q)
    finally:
        controller.stop()


def read_output(out_stream: BinaryIO, out_queue: Queue) -> None:
    for line in iter(out_stream.readline, b""):
        out_queue.put(line.decode("utf-8"))
    out_stream.close()


def make_program(extra_env: dict) -> Tuple[Popen, Thread, Queue]:
    env = os.environ.copy()
    env.update(extra_env)
    program = ["python", "ensembl/production/reporting/amqp_reporter.py"]
    queue: Queue = Queue()
    sub_p = Popen(program, stdout=PIPE, env=env)
    reader_t = Thread(target=read_output, args=(sub_p.stdout, queue))
    reader_t.daemon = True
    queue_gen = queue_consumer(queue)
    return sub_p, reader_t, queue_gen


@pytest.fixture
def program_out_es(elastic_search):
    extra_env = {
        "REPORTER_TYPE": "elasticsearch",
    }
    sub_p, reader_t, queue_gen = make_program(extra_env)
    reader_t.start()
    try:
        yield queue_gen
    finally:
        sub_p.terminate()
        reader_t.join(5)


@pytest.fixture
def program_out_smtp():
    extra_env = {
        "REPORTER_TYPE": "email",
        "SMTP_PORT": str(SMTP_TEST_SERVER_PORT)
    }
    sub_p, reader_t, queue_gen = make_program(extra_env)
    reader_t.start()
    try:
        yield queue_gen
    finally:
        sub_p.terminate()
        reader_t.join(5)


def test_validate_payload_ok():
    valid_message = '{"hello": "world"}'
    payload = validate_payload(valid_message)
    assert payload == {"hello": "world"}


@pytest.mark.parametrize(
    "invalid_message",
    ['"This is a valid JSON but not an object"', "This is not a JSON"],
)
def test_validate_payload_raise(invalid_message):
    with pytest.raises(ValueError):
        validate_payload(invalid_message)


def test_compose_email_ok(valid_email_message):
    message = compose_email(valid_email_message)
    assert message["Subject"] == valid_email_message["subject"]
    assert message["From"] == config.smtp_user
    assert message["To"] == ", ".join(valid_email_message["to"])
    assert message.get_content().strip() == valid_email_message["content"]


@pytest.mark.parametrize(
    "invalid_email_message",
    [
        {},
        {
            "to": ["you@org.com"],
            "subject": "This email is missing content",
        },
    ],
)
def test_compose_email_raises(invalid_email_message):
    with pytest.raises(ValueError):
        compose_email(invalid_email_message)


def test_consume_and_es_post_success(amqp_publish, elastic_search, program_out_es):
    message = {"message": "hello!"}
    amqp_publish(message)
    for line in program_out_es:
        if "Acked:" in line:
            break
    else:
        assert False, "Reached end of output without acking the message"
    res = elastic_search({"query": {"query_string": {"query": 'message:"hello!"'}}})
    first_res = res["hits"]["hits"][0]
    assert first_res["_source"] == message


def test_consume_and_reject(amqp_publish, program_out_es):
    message = "Invalid Message"
    amqp_publish(message)
    for line in program_out_es:
        if "Acked:" in line:
            assert False, "Should not have acked the message!"
        if "Rejected:" in line:
            break
    else:
        assert False, "Reached end of output without acking the message"


def test_consume_and_es_post_after_reject(amqp_publish, elastic_search, program_out_es):
    message = "Invalid Message"
    amqp_publish(message)
    for line in program_out_es:
        if "Acked:" in line:
            assert False, "Should not have acked the message!"
        if "Rejected:" in line:
            break
    else:
        assert False, "Reached end of output without acking the message"
    message = {"message": "hello!"}
    amqp_publish(message)
    for line in program_out_es:
        if "Acked:" in line:
            break
    else:
        assert False, "Reached end of output without acking the message"
    res = elastic_search({"query": {"query_string": {"query": 'message:"hello!"'}}})
    first_res = res["hits"]["hits"][0]
    assert first_res["_source"] == message


def test_consume_and_sendmail_success(
    amqp_publish, smtp_messages, program_out_smtp, valid_email_message
):
    message = valid_email_message
    amqp_publish(message)
    for line in program_out_smtp:
        if "Acked:" in line:
            break
    else:
        assert False, "Reached end of output without acking the message"
    for received_smtp in smtp_messages:
        received_email = EmailParser(policy=default_policy).parsebytes(
            received_smtp.message
        )
        assert received_smtp.sender == config.smtp_user
        assert received_email["from"] == config.smtp_user
        assert received_smtp.receivers == message["to"]
        assert received_email["to"] == ", ".join(message["to"])
        assert received_smtp.remote_host[0] == SMTP_TEST_SERVER_HOST
        assert received_email["subject"] == message["subject"]
        assert received_email.get_content().strip() == message["content"]
        break
    else:
        assert False, "No messages received"
