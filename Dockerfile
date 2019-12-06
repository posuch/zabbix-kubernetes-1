FROM python:3.7-alpine
LABEL Description="zabbix-kubernetes - efficent kubernetes monitoring for zabbix"

MAINTAINER operations@vico-research.com

COPY --chown=nobody:users . /app
RUN pip install -r /app/requirements.txt

USER nobody
WORKDIR /app

ENTRYPOINT [ "/app/check_kubernetesd" ]
