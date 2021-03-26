EnsEMBL - Production Reporting Applications
===========================================

This repository contains Python applications used for reporting status and
outcomes of EnsEMBL Production Pipelines and other internal Production services.


AMQP Reporter
-------------

Python AMQP consumer designed to consume JSON messages from a RabbitMQ queue and
either store them in a Elasticsearch instance or sent via email.


### Requirements

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
  "body": "Hello!"
}
```


### Usage

AMQP Reporter runs in the foreground and can be started with the command:

```
python ensembl/production/reporting/amqp_reporter.py
```

