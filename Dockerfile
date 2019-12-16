FROM python:3.8.0-buster
LABEL Description="zabbix-kubernetes - efficent kubernetes monitoring for zabbix"

MAINTAINER operations@vico-research.com

COPY --chown=nobody:users . /app
RUN pip install -r /app/requirements.txt \
       mv /app/config_example.py config_default.py

USER nobody
WORKDIR /app

ENTRYPOINT [ "/app/check_kubernetesd" ]
