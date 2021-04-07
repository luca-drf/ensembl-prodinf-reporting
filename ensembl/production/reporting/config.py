import configparser
import os
from typing import NamedTuple


def parse_debug_var(var: str):
    return not ((var.lower() in ("f", "false", "no", "none")) or (not var))


class Config(NamedTuple):
    debug: bool
    reporter_type: str
    amqp_uri: str
    amqp_queue: str
    amqp_prefetch: int
    es_host: str
    es_port: int
    es_index: str
    es_doc_type: str
    smtp_host: str
    smtp_port: int


parser = configparser.ConfigParser()
parser.read(".config.ini")
section = os.environ.get("CONFIG_SECTION", "DEFAULT")
file_config = parser[section]


debug_var = os.environ.get("DEBUG", file_config.get("debug", "false"))


config = Config(
    debug=parse_debug_var(debug_var),
    reporter_type=os.environ.get(
        "REPORTER_TYPE", file_config.get("reporter_type", "elasticsearch")
    ),
    amqp_uri=os.environ.get("AMQP_URI", file_config.get("amqp_uri", "amqp://")),
    amqp_queue=os.environ.get("AMQP_QUEUE", file_config.get("amqp_queue", "test")),
    amqp_prefetch=int(
        os.environ.get("AMQP_PREFETCH_COUNT", file_config.get("amqp_prefetch_count"))
    ),
    es_host=os.environ.get("ES_HOST", file_config.get("es_host", "localhost")),
    es_port=int(os.environ.get("ES_PORT", file_config.get("es_port", "9200"))),
    es_index=os.environ.get("ES_INDEX", file_config.get("es_index", "test")),
    es_doc_type=os.environ.get("ES_DOC_TYPE", file_config.get("es_doc_type", "test")),
    smtp_host=os.environ.get("SMTP_HOST", file_config.get("smtp_host", "127.0.0.1")),
    smtp_port=int(os.environ.get("SMTP_PORT", file_config.get("smtp_port", "25"))),
)
