EnsEMBL - Production Reporting Applications
===========================================

Python applications used for reporting status and outcomes of EnsEMBL Production
Pipelines and other internal Production services.

[![Build Status](https://travis-ci.com/Ensembl/ensembl-prodinf-reporting.svg?token=5zKzvNKrmopKSdQGqBxH&branch=main)](https://travis-ci.com/Ensembl/ensembl-prodinf-reporting) [![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/Ensembl/ensembl-prodinf-reporting/blob/main/LICENSE)


AMQP Reporter
-------------

Python AMQP consumer designed to consume JSON messages from a RabbitMQ queue and
either store them in a Elasticsearch instance or sent via email.


### System Requirements

- Python 3.8+
- Elasticsearch 7.12
- RabbitMQ 3


### Configuration

AMQP Reporter can be configured by modifying its config file `.config.ini`.
Multiple configurations can be specified in the config file using sections.
Only one section can be loaded and is selected via the environment variable
`CONFIG_SECTION`, if the latter is not set, `DEFAULT` section will be loaded.
For example, setting `CONFIG_SECTION=copy_reporter` will load the variables
contained in the section named `copy_reporter`.

Variables loaded via `.config.ini` can be overriden by setting variables with
same names on the environment. Environment variables always take precence.

Set `REPORTER_TYPE=elasticsearch` for consuming messages and posting them on the
configured Elasticsearch index.

Set `REPORTER_TYPE=email` to consume a message and send an email instead. The
message on the queue must have the following structure:

```
{
  "subject": "My Subject",
  "from": "the.sender@email.org",
  "to": ["receiver1@email.org", "receiver2@email.org"],
  "content": "Hello!"
}
```


### Usage

AMQP Reporter runs in the foreground and can be started with the command:

```
python ensembl/production/reporting/amqp_reporter.py
```
Or by running a Docker container (see `Dockerfile`)



### Testing

Integration tests are implemented using Docker Compose and Pytest. Please refer
to `.travis.yml` for more insight on tests configuration.

In order to run tests manually, run the following commands:
```
pip install -r requirements-test.txt
pip install .
docker-compose -f tests/docker-compose.yml up -d
pytest tests
```

Please wait for Docker Compose to finish building the images on the first run
before running the tests with Pytest.
