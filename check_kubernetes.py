#!/usr/bin/env python3
""" kubernetes zabbix monitoring tries to read config from file (host, port, token)
    action [discover, get]
    resource [deployment, service]
    key [ready]

    cache results for each <config>_<resource>.json 1min
"""
import os
import sys
import importlib.util
import json

from kubernetes import client, config

if len(sys.argv) < 5:
    print("kubernetes <CONFIG_NAME> <ACTION> <RESOURCE> <KEY>")
    sys.exit(1)

config_name = sys.argv[1]
action = sys.argv[2]
resource = sys.argv[3]
key = sys.argv[4]

try:
    config = importlib.import_module(config_name)
except ImportError:
    print("config file %s not found. ABORTING!" % config_name)
    sys.exit(1)

server = 'https://%s:%s' % (config.host, config.port)

api_configuration = client.Configuration()
api_configuration.host = server
api_configuration.verify_ssl = False
api_configuration.api_key = {"authorization": "Bearer " + config.token}

api_client = client.ApiClient(api_configuration)

# core_v1 = client.CoreV1Api(api_client)
# apps_v1 = client.AppsV1Api(api_client)
#
# ret = core_v1.list_pod_for_all_namespaces(watch=False)


def discover(resource):
    pass


def get(resource, key):
    pass
