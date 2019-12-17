FROM python:3.7-alpine
LABEL Description="zabbix-kubernetes - efficent kubernetes monitoring for zabbix"

MAINTAINER operations@vico-research.com

ENV K8S_API_HOST ""
ENV K8S_API_TOKEN ""
ENV ZABBIX_SERVER "zabbix"
ENV ZABBIX_HOST "k8s"


COPY --chown=nobody:users . /app
RUN  apk update && \
       apk add build-base libffi-dev libffi openssl-dev && \
       pip install -r /app/requirements.txt && \
       apk upgrade --update-cache --available && \
       apk del build-base openssl-dev libffi-dev && \
       rm -rf /var/cache/apk/ && \
       mv /app/config_example.py /app/config_default.py 

USER nobody
WORKDIR /app

ENTRYPOINT [ "/app/check_kubernetesd" ]
