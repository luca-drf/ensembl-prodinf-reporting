import asyncio
from email.parser import BytesParser as EmailParser
from email.policy import default as default_policy
import logging
from threading import Thread
import os
from queue import Queue, Empty
from subprocess import Popen, PIPE
from typing import BinaryIO, NamedTuple, List, Tuple

from aiosmtpd.controller import Controller as SMTPController
from elasticsearch import Elasticsearch
import kombu
import urllib3

import pytest

from ensembl.production.reporting.config import config


SMTP_HOST = '127.0.0.1'
SMTP_PORT = 10025


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
    retry = urllib3.Retry(total=retries, backoff_factor=backoff,
                          status_forcelist=[404, 500, 502, 503, 504])
    manager = urllib3.PoolManager(retries=retry)
    manager.request('GET', url)


@pytest.fixture
def smtp_messages():
    message_q: Queue = Queue()

    class SMTPHandler:
        async def handle_DATA(self, _server, session, envelope):
            peer = session.peer
            mail_from = envelope.mail_from
            rcpt_tos = envelope.rcpt_tos
            data = envelope.content  # type: bytes
            msg = SMTPMessage(peer, mail_from, rcpt_tos, data)
            message_q.put(msg)
            return '250 OK'

    controller = SMTPController(SMTPHandler(), hostname=SMTP_HOST, port=SMTP_PORT)
    controller.start()
    try:
        yield queue_consumer(message_q)
    finally:
        controller.stop()


def read_output(out_stream: BinaryIO, out_queue: Queue) -> None:
    for line in iter(out_stream.readline, b''):
        out_queue.put(line.decode('utf-8'))
    out_stream.close()


def make_program(extra_env: dict) -> Tuple[Popen, Thread, Queue]:
    env = os.environ.copy()
    env.update(extra_env)
    program = ['python', 'ensembl/production/reporting/amqp_reporter.py']
    queue: Queue = Queue()
    sub_p = Popen(program, stdout=PIPE, env=env)
    reader_t = Thread(target=read_output, args=(sub_p.stdout, queue))
    reader_t.daemon = True
    queue_gen = queue_consumer(queue)
    return sub_p, reader_t, queue_gen


@pytest.fixture
def program_out_es():
    extra_env = {
        'REPORTER_TYPE': 'elasticsearch',
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
        'REPORTER_TYPE': 'email',
        'SMTP_PORT': str(SMTP_PORT)
    }
    sub_p, reader_t, queue_gen = make_program(extra_env)
    reader_t.start()
    try:
        yield queue_gen
    finally:
        sub_p.terminate()
        reader_t.join(5)


@pytest.fixture
def amqp_publish():
    wait_for(f"http://{config.amqp_host}:{config.amqp_port}/")
    connection = kombu.Connection(
        f"amqp://{config.amqp_user}:{config.amqp_pass}@{config.amqp_host}:{config.amqp_port}/{config.amqp_virtual_host}"
    )
    exchange = kombu.Exchange('test_exchange', type='topic')
    queue = kombu.Queue('test_queue', exchange)
    producer = connection.Producer()

    def publisher(message: dict) -> None:
        producer.publish(message,
                         exchange=exchange,
                         declare=[queue],
                         retry=True,
                         serializer='json',
                         delivery_mode=2)
    return publisher


@pytest.fixture
def elastic_search():
    wait_for(f"http://{config.es_host}:{config.es_port}/")
    es = Elasticsearch([{"host": config.es_host, "port": config.es_port}])

    def search(body: dict) -> None:
        es.indices.flush()
        es.indices.refresh()
        return es.search(index='test', body=body)

    yield search
    es.indices.delete('test')


def test_consume_and_es_post_success(amqp_publish, elastic_search, program_out_es):
    message = {'message': 'hello!'}
    amqp_publish(message)
    for line in program_out_es:
        if "Acked:" in line:
            break
    else:
        assert False, "Reached end of output without acking the message"
    res = elastic_search({"query": {
                             "query_string": {
                                 "query": "message:\"hello!\""
                             }
                         }})
    first_res = res['hits']['hits'][0]
    assert first_res['_source'] == message


def test_consume_and_sendmail_success(amqp_publish, smtp_messages, program_out_smtp):
    message = {
        'subject': 'Test Email',
        'from': 'sender@email.org',
        'to': ['receiver1@email.org', 'receiver2@email.org'],
        'body': 'Hello!',
    }
    amqp_publish(message)
    for line in program_out_smtp:
        if "Acked:" in line:
            break
    else:
        assert False, "Reached end of output without acking the message"
    for received_smtp in smtp_messages:
        received_email = EmailParser(policy=default_policy).parsebytes(received_smtp.message)
        assert received_smtp.sender == message['from']
        assert received_email['from'] == message['from']
        assert received_smtp.receivers == message['to']
        assert received_email['to'] == ', '.join(message['to'])
        assert received_smtp.remote_host[0] == SMTP_HOST
        assert received_email['subject'] == message['subject']
        assert received_email.get_content().strip() == message['body']
        break
