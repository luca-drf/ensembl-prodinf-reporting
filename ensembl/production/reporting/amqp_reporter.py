from contextlib import contextmanager
from email.message import EmailMessage
import logging
import json
import signal
from smtplib import SMTP, SMTPException
import sys
from typing import Any

from kombu import Connection, Queue, Producer, Consumer, Message
from kombu.asynchronous import Hub
from elasticsearch import Elasticsearch, ElasticsearchException

from ensembl.production.reporting.config import config


LOG_LEVEL = logging.DEBUG if config.debug else logging.INFO
logging.basicConfig(
    stream=sys.stdout,
    format="%(asctime)s %(levelname)-8s %(name)-15s: %(message)s",
    level=LOG_LEVEL,
)

logger = logging.getLogger("amqp_reporter")


queue = Queue(config.amqp_queue)
AMQP_URI = f"amqp://{config.amqp_user}:{config.amqp_pass}@{config.amqp_host}:{config.amqp_port}/{config.amqp_virtual_host}"
conn = Connection(AMQP_URI)
hub = Hub()


def validate_payload(message_body: Any) -> dict:
    try:
        payload = json.loads(message_body)
    except json.JSONDecodeError as err:
        msg = f"Cannot decode JSON message. {err}."
        raise ValueError(msg) from err
    if not isinstance(payload, dict):
        msg = f"Invalid message type: JSON message must be of type 'object'."
        raise ValueError(msg)
    return payload


@contextmanager
def es_reporter():
    es = Elasticsearch([{"host": config.es_host, "port": config.es_port}])
    if not es.ping():
        logger.error(
            "Cannot connect to Elasticsearch server. Host: %s, Port: %s",
            config.es_host,
            config.es_port,
        )

    def on_message(message: Message):
        logger.debug("From queue: %s, received: %s", config.amqp_queue, message.body)
        try:
            validate_payload(message.body)
        except ValueError as err:
            logger.error("%s Message: %s", err, message.body)
            message.reject()
            logger.warning("Rejected: %s", message.body)
            return
        try:
            es.index(
                index=config.es_index, body=message.body, doc_type=config.es_doc_type
            )
        except ElasticsearchException as err:
            logger.error("Cannot modify index %s. Error: %s", config.es_index, err)
            message.reject()
            logger.warning("Rejected: %s", message.body)
            return
        logger.debug(
            "To index: %s, type: %s, document: %s",
            config.es_index,
            config.es_doc_type,
            message.body,
        )
        message.ack()
        logger.debug("Acked: %s", message.body)

    try:
        yield on_message
    finally:
        try:
            es.close()
        except AttributeError:
            pass


def compose_email(email: dict) -> EmailMessage:
    msg = EmailMessage()
    try:
        msg["Subject"] = email["subject"]
        msg["From"] = email["from"]
        msg["To"] = email["to"]  # This can be a list of str
        msg.set_content(email["content"])
    except KeyError as err:
        raise ValueError(f"Cannot parse message. Invalid key: {err}.")
    return msg


@contextmanager
def smtp_reporter():
    try:
        with SMTP(host=config.smtp_host, port=config.smtp_port) as smtp:
            smtp.noop()
    except (ConnectionRefusedError, SMTPException) as err:
        logger.error(
            "Cannot connect to SMTP server: %s Host: %s, Port: %s",
            err,
            config.smtp_host,
            config.smtp_port,
        )

    def on_message(message: Message):
        logger.debug("From queue: %s, received: %s", config.amqp_queue, message.body)
        try:
            email = validate_payload(message.body)
            msg = compose_email(email)
        except ValueError as err:
            logger.error("%s Email Message: %s", err, email)
            message.reject()
            logger.warning("Rejected: %s", message.body)
            return
        try:
            with SMTP(host=config.smtp_host, port=config.smtp_port) as smtp:
                smtp.send_message(msg)
        except SMTPException as err:
            logger.error("Cannot send email message: %s Message: %s", err, email)
            message.reject()
            logger.warning("Rejected: %s", message.body)
            return
        logger.info("Email sent: %s", email)
        message.ack()
        logger.debug("Acked: %s", message.body)

    yield on_message


def stop_gracefully():
    hub.close()
    conn.release()
    hub.stop()


def sigint_handler(_signum, _frame):
    logger.info("Received SIGINT. Terminating.")
    stop_gracefully()


def sigterm_handler(_signum, _frame):
    logger.info("Received SIGTERM. Terminating.")
    stop_gracefully()


def main():
    signal.signal(signal.SIGINT, sigint_handler)
    signal.signal(signal.SIGTERM, sigterm_handler)
    try:
        conn.register_with_event_loop(hub)
    except ConnectionRefusedError as err:
        logger.critical("Cannot connect to %s", AMQP_URI)
        logger.critical("Exiting.")
        sys.exit(1)

    logger.info("Configuration: %s", config)
    if config.reporter_type == "elasticsearch":
        report = es_reporter()
    elif config.reporter_type == "email":
        report = smtp_reporter()
    with report as on_message_report:
        with Consumer(
            conn,
            [queue],
            prefetch_count=config.amqp_prefetch,
            on_message=on_message_report,
            auto_declare=False
        ):
            logger.info("Starting main loop")
            hub.run_forever()


if __name__ == "__main__":
    main()
