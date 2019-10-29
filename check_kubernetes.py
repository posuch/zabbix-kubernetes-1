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

KNOWN_ACTIONS = ['discover', 'get']
KNOWN_RESOURCES = ['deployments', 'services']

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

if action not in KNOWN_ACTIONS:
    print("action '%s' not found in known list. ABORTING!")
    sys.exit(1)

if resource not in KNOWN_RESOURCES:
    print("resource '%s' not found in known list. ABORTING!")
    sys.exit(1)


class CheckKubernetes:
    def __init__(self, config):
        self.server = 'https://%s:%s' % (config.host, config.port)

        self.api_configuration = client.Configuration()
        self.api_configuration.host = self.server
        self.api_configuration.verify_ssl = False
        self.api_configuration.api_key = {"authorization": "Bearer " + config.token}

        self.api_client = client.ApiClient(self.api_configuration)

    def discover_deployments(self, key):
        apps_v1 = client.AppsV1Api(self.api_client)
        ret = apps_v1.list_deployment_for_all_namespaces(watch=False)

    def get_deployments(self, resource, key):
        pass


# core_v1 = client.CoreV1Api(api_client)

# ret = core_v1.list_pod_for_all_namespaces(watch=False)
instance = CheckKubernetes(config)
getattr(instance, action + '_' + resource)(key)

