from contextlib import contextmanager
from email.message import EmailMessage
import logging
import json
import signal
from smtplib import SMTP, SMTPException
import sys
from kombu import Connection, Queue, Producer, Consumer, Message
from kombu.asynchronous import Hub
from elasticsearch import Elasticsearch, ElasticsearchException
from ensembl.production.reporting.config import config


LOG_LEVEL = logging.DEBUG if config.debug else logging.INFO
logging.basicConfig(stream=sys.stdout,
                    format="%(asctime)s %(levelname)-8s %(name)-15s --: %(message)s",
                    level=LOG_LEVEL)

logger = logging.getLogger('amqp_reporter')


queue = Queue(config.amqp_queue)
conn = Connection(config.amqp_uri)
hub = Hub()


@contextmanager
def es_reporter():
    es = Elasticsearch([{'host': config.es_host, 'port': config.es_port}])

    def on_message(message: Message):
        logger.debug('From queue: %s, received: %s',
                      config.amqp_queue, message.body)
        try:
            json.loads(message.body)
        except json.JSONDecodeError as err:
            logger.critical('Cannot decode JSON message: %s', message.body)
            message.reject()
            logger.debug('Rejected: %s', message.body)
            return
        try:
            es.index(index=config.es_index,
                     body=message.body,
                     doc_type=config.es_doc_type)
        except ElasticsearchException as err:
            logger.critical('Cannot modify index %s. Error: %s', config.es_index, err)
            message.requeue()
            logger.debug('Requeued: %s', message.body)
            return
        logger.debug('To index: %s, type: %s, document: %s',
                      config.es_index, config.es_doc_type, message.body)
        message.ack()
        logger.debug('Acked: %s', message.body)

    try:
        yield on_message
    finally:
        es.close()


def compose_email(email: dict):
    msg = EmailMessage()
    msg['Subject'] = email['subject']
    msg['From'] = email['from']
    msg['To'] = email['to']  # This can be a list of str
    msg.set_content(email['body'])
    return msg


@contextmanager
def smtp_reporter():
    smtp = SMTP(host=config.smtp_host, port=config.smtp_port)

    def on_message(message: Message):
        logger.debug('From queue: %s, received: %s',
                      config.amqp_queue, message.body)
        try:
            email = json.loads(message.body)
        except json.JSONDecodeError as err:
            logger.critical('Cannot decode JSON message: %s', message.body)
            message.reject()
            logger.debug('Rejected: %s', message.body)
            return
        msg = compose_email(email)
        try:
            smtp.send_message(msg)
        except SMTPException as err:
            logger.critical('Unable to send email message: %s', message.body)
            message.requeue()
            logger.debug('Requeued: %s', message.body)
            return
        logger.debug('Email sent: %s', message.body)
        message.ack()
        logger.debug('Acked: %s', message.body)

    try:
        yield on_message
    finally:
        smtp.quit()


def sigint_handler(_signum, _frame):
    logger.info('Received SIGINT. Terminating.')
    hub.stop()


def sigterm_handler(_signum, _frame):
    logger.info('Received SIGTERM. Terminating.')
    hub.stop()


if __name__ == '__main__':
    signal.signal(signal.SIGINT, sigint_handler)
    signal.signal(signal.SIGTERM, sigterm_handler)
    conn.register_with_event_loop(hub)

    logger.info('Configuration: %s', config)
    if config.reporter_type == 'elasticsearch':
        report = es_reporter()
    elif config.reporter_type == 'email':
        report = smtp_reporter()
    with report as on_message_report:
        with Consumer(conn, [queue],
                      prefetch_count=config.amqp_prefetch,
                      on_message=on_message_report):
            logger.info('Starting main loop')
            hub.run_forever()
