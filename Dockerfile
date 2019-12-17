FROM python:3.8.0-buster
LABEL Description="zabbix-kubernetes - efficent kubernetes monitoring for zabbix"

MAINTAINER operations@vico-research.com

ENV K8S_API_HOST ""
ENV K8S_API_TOKEN ""
ENV ZABBIX_SERVER "zabbix"
ENV ZABBIX_HOST "k8s"

COPY --chown=nobody:users . /app
RUN pip install -r /app/requirements.txt && \
       mv /app/config_example.py /app/config_default.py

USER nobody
WORKDIR /app

ENTRYPOINT [ "/app/check_kubernetesd", "config_default" ]
