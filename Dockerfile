# pull official base image
FROM python:3.8.9-slim

# set work directory
WORKDIR /usr/src/app

# set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# copy all in the workdir
COPY . /usr/src/app/

# install python packages
RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "ensembl/production/reporting/amqp_reporter.py"]
