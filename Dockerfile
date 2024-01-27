
 
FROM python:3.10-slim-buster
LABEL version="0.0.0" maintainer="topaz@free.fr"
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update
RUN apt-get -y install python3-pip bash

# install files and requirements
RUN mkdir /app
ADD run.sh /app
ADD server.py /app
ADD requirements.txt /app
ADD templates /app/templates
RUN pip3 install --requirement /app/requirements.txt

# prepare entry point
RUN chmod 755 /app/run.sh
RUN chmod 777 /app/

EXPOSE 8080

ENTRYPOINT ["/app/run.sh"]
