import re
import sys
import json
import base64
import logging

import time
import threading
from pprint import pprint

from pyzabbix import ZabbixAPI, ZabbixMetric, ZabbixSender, ZabbixResponse, ZabbixAPIException
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from datetime import datetime, date, timedelta
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from modules.timed_threads import TimedThread

exit_flag = threading.Event()


class CheckKubernetesDaemon:
    def __init__(self, config, config_name, resources, discovery_interval, data_interval):

        self.logger = logging.getLogger(self.__class__.__name__)
        self.config_name = config_name
        self.discovery_interval = discovery_interval
        self.data_interval = data_interval

        self.api_configuration = client.Configuration()
        self.api_configuration.host = config.k8s_api_host
        self.api_configuration.verify_ssl = config.verify_ssl
        self.api_configuration.api_key = {"authorization": "Bearer " + config.k8s_api_token}

        self.api_client = client.ApiClient(self.api_configuration)
        self.core_v1 = client.CoreV1Api(self.api_client)
        self.apps_v1 = client.AppsV1Api(self.api_client)
        self.zabbix_sender = ZabbixSender(zabbix_server=config.zabbix_server)
        self.zabbix_host = config.zabbix_host

        self.resources = resources

        self.logger.info("INIT ==> K8S API Server: %s, Zabbix Server: %s, Zabbix Host: %s : %s" %
                         (self.api_configuration.host, config.zabbix_server, self.zabbix_host, ",".join(self.resources)))

        self.data = dict()
        self.data_refreshed = None

    @staticmethod
    def slugit(name, maxlen):
        if len(name) <= maxlen:
            return name

        prefix_pos = int((maxlen / 2) - 1)
        suffix_pos = len(name) - int(maxlen / 2) - 2
        return name[:prefix_pos] + "~" + name[suffix_pos:]

    def handler(self, signum, *args):
        self.logger.info('Signal handler called with signal %s... stopping (max %s seconds)' % (signum, 3))
        exit_flag.set()
        self.discover_thread.join(timeout=3)
        self.data_thread.join(timeout=3)
        self.self.logger.info('All threads exited... exit check_kubernetesd')
        sys.exit(0)

    def run(self):
        self.discover_thread = TimedThread('discover_thread', self.discovery_interval, exit_flag,
                                           daemon=self, daemon_method='send_discovery_to_zabbix')
        self.data_thread = TimedThread('data_thread', self.data_interval, exit_flag,
                                       daemon=self, daemon_method='send_data_to_zabbix')

        self.discover_thread.start()
        time.sleep(5)
        self.data_thread.start()

    def check_or_refresh_data(self):
        now = datetime.now()
        if not self.data_refreshed or self.data_refreshed + timedelta(seconds=60) < now:
            self.refresh_data()

        if not self.data_refreshed or self.data_refreshed + timedelta(seconds=60) < now:
            self.self.logger.error('could not refresh data')
            return False
        return True

    def refresh_data(self):
        for resource in self.resources:
            try:
                self.data[resource] = self.get_data(resource)
            except ApiException as e:
                self.self.logger.error('ApiException occured: %s' % str(e))
                return False

        self.data_refreshed = datetime.now()
        return True

    def get_data(self, resource):
        if resource in ['nodes', 'components', 'tls', 'pods', 'services']:
            # use core_v1
            api = self.core_v1
        elif resource in ['deployments']:
            # use apps_v1
            api = self.apps_v1
        else:
            raise AttributeError('No valid resource found: %s' % resource)

        if resource == 'nodes':
            return api.list_node(watch=False).to_dict()
        elif resource == 'deployments':
            return api.list_deployment_for_all_namespaces(watch=False).to_dict()
        elif resource == 'components':
            return api.list_component_status(watch=False).to_dict()
        elif resource == 'tls':
            return api.list_secret_for_all_namespaces(watch=False).to_dict()
        elif resource == 'pods':
            return api.list_pod_for_all_namespaces(watch=False).to_dict()
        elif resource == 'services':
            return api.list_service_for_all_namespaces(watch=False).to_dict()

    def transform_value(self, value):
        if value is None:
            return 0
        m = re.match(r"^(\d+)Ki$", str(value))
        if m:
            return int(m.group(1)) * 1024
        return value

    def send_discovery_to_zabbix(self):
        if not self.check_or_refresh_data():
            return

        metrics = list()
        for resource in self.resources:
            zabbix_data = getattr(self, 'discover_' + resource)()
            if len(zabbix_data) == 0:
                continue
            metrics.append(ZabbixMetric(self.zabbix_host, 'check_kubernetesd[discover,' + resource + ']', zabbix_data))
        metrics.append(ZabbixMetric(self.zabbix_host, 'check_kubernetesd[discover,api]', int(time.time())))
        result = self.zabbix_sender.send(metrics)
        if result.failed > 0:
            self.logger.error("failed to sent %s discoveries" % len(metrics))
        else:
            self.logger.info("successfully sent %s discoveries" % len(metrics))

    def send_data_to_zabbix(self):
        if not self.check_or_refresh_data():
            return

        metrics = list()
        for resource in self.resources:
            data_to_send = getattr(self, 'get_' + resource)()
            metrics += data_to_send
        metrics.append(ZabbixMetric(self.zabbix_host, 'check_kubernetesd[get,items]', int(time.time())))

        result = self.zabbix_sender.send(metrics)

        if result.failed > 0:
            self.logger.error("failed to sent %s items" % len(metrics))
        else:
            self.logger.info("successfully sent %s items" % len(metrics))

    def discover_nodes(self):
        name_list = []
        for node in self.data.get('nodes').get('items'):
            name_list.append({
                "{#NAME}": node['metadata']['name'],
            })
        return json.dumps({"data": name_list})

    def discover_deployments(self):
        name_list = []
        for deployment in self.data.get('deployments').get('items'):
            name_list.append({
                "{#NAME}": deployment['metadata']['name'],
                "{#NAMESPACE}": deployment['metadata']['namespace'],
                "{#SLUG}": CheckKubernetesDaemon.slugit(deployment['metadata']['namespace'] + "/" + deployment['metadata']['name'], 40),
            })
        return json.dumps({"data": name_list})

    def discover_services(self):
        name_list = []
        return name_list
        # for service in self.data.get('services').get('items'):
        #    name_list.append({
        #        "{#NAME}": service['metadata']['name'],
        #        "{#NAMESPACE}": service['metadata']['namespace'],
        #        "{#SLUG}": CheckKubernetesDaemon.slugit(service['metadata']['namespace'] + "/" + service['metadata']['name'], 40),
        #    })
        # return json.dumps({"data": name_list})

    def discover_pods(self):
        collect = {}
        for pod in self.data.get('pods').get('items'):
            for container in pod['spec']['containers']:
                namespace = pod['metadata']['namespace']
                collect.setdefault(namespace, {})
                collect[namespace].setdefault(container['name'], 0)
                collect[namespace][container['name']] += 1

        name_list = []
        for namespace, data in collect.items():
            for container, count in data.items():
                name_list.append({
                    "{#NAMESPACE}": namespace,
                    "{#NAME}": container
                })
        return json.dumps({"data": name_list})

    def discover_components(self):
        name_list = []
        for component in self.data.get('components').get('items'):
            name_list.append({
                "{#NAME}": component['metadata']['name'],
            })
        return json.dumps({"data": name_list})

    def discover_tls(self):
        name_list = []
        for tls_cert in self.data.get('tls').get('items'):
            if tls_cert["data"] is None:
                continue
            if "tls.crt" in dict(tls_cert["data"]):
                name_list.append({
                    "{#NAME}": tls_cert['metadata']['name'],
                    "{#NAMESPACE}": tls_cert['metadata']['namespace'],
                })
        return json.dumps({"data": name_list})

    def get_nodes(self):
        data_to_send = list()
        for node in self.data.get('nodes').get('items'):
            node_name = node['metadata']['name']

            failed_conds = []
            for cond in node['status']['conditions']:
                if cond['type'].lower() == "ready":
                    data_to_send.append(ZabbixMetric(self.zabbix_host, 'check_kubernetesd[get,nodes,' + node_name + ',available_status]',
                                                     'not available' if cond['status'] != 'True' else 'OK'))
                else:
                    if cond['status'] == 'True':
                        failed_conds.append(cond['type'])

            data_to_send.append(ZabbixMetric(self.zabbix_host, 'check_kubernetesd[get,nodes,' + node_name + ',condition_status_failed]',
                                             failed_conds if len(failed_conds) > 0 else 'OK'))

            for monitor_value in ['allocatable.cpu',
                                  'allocatable.ephemeral-storage',
                                  'allocatable.memory',
                                  'allocatable.pods',
                                  'capacity.cpu',
                                  'capacity.ephemeral-storage',
                                  'capacity.memory',
                                  'capacity.pods']:
                current_indirection = node['status']
                for key in monitor_value.split("."):
                    current_indirection = current_indirection[key]
                data_to_send.append(ZabbixMetric(
                    self.zabbix_host, 'check_kubernetesd[get,nodes,%s,%s]' % (node_name, monitor_value),
                    self.transform_value(current_indirection))
                )

        return data_to_send

    def get_deployments(self):
        data_to_send = list()
        for deployment in self.data.get('deployments').get('items'):
            deployment_name = deployment['metadata']['name']
            deployment_namespace = deployment['metadata']['namespace']
            for status_type in deployment['status']:
                if status_type == 'conditions':
                    continue

                data_to_send.append(ZabbixMetric(
                    self.zabbix_host, 'check_kubernetesd[get,deployments,%s,%s,%s]' % (deployment_namespace, deployment_name, status_type),
                    self.transform_value(deployment['status'][status_type]))
                )

            failed_conds = []
            for cond in [x for x in deployment['status']['conditions'] if x['type'].lower() == "available"]:
                if cond['status'] != 'True':
                    failed_conds.append(cond['type'])

            data_to_send.append(ZabbixMetric(
                self.zabbix_host, 'check_kubernetesd[get,deployments,%s,%s,available_status]' % (deployment_namespace, deployment_name),
                failed_conds if len(failed_conds) > 0 else 'OK')
            )
        return data_to_send

    def get_services(self):
        data_to_send = list()
        num_services = 0
        num_ingress_services = 0
        for service in self.data.get('services').get('items'):
            num_services += 1
            service_name = service['metadata']['name']
            service_namespace = service['metadata']['namespace']
            if service["status"]["load_balancer"]["ingress"] is not None:
                num_ingress_services += 1

        data_to_send.append(ZabbixMetric(self.zabbix_host, 'check_kubernetes[get,services,num_services]', num_services))
        data_to_send.append(ZabbixMetric(self.zabbix_host, 'check_kubernetes[get,services,num_ingress_services]', num_ingress_services))
        return data_to_send

    def get_pods(self):
        collect = {}
        data_to_send = list()
        for pod in self.data.get('pods').get('items'):
            pods_name = pod['metadata']['name']
            pods_namespace = pod['metadata']['namespace']
            container_name = None

            if "container_statuses" in pod['status'] and pod['status']['container_statuses']:
                for container in pod['status']['container_statuses']:
                    container_name = container['name']
                    collect.setdefault(pod['metadata']['namespace'], {})
                    collect[pod['metadata']['namespace']].setdefault(
                        container_name,
                        {
                            "restart_count": 0,
                            "ready": 0,
                            "not_ready": 0,
                        }
                    )
                    collect[pods_namespace][container_name]["restart_count"] += container['restart_count']

                    if container['ready'] is True:
                        collect[pods_namespace][container_name]["ready"] += 1
                    else:
                        collect[pods_namespace][container_name]["not_ready"] += 1

        for pods_namespace, pod_data in collect.items():
            for container_name, data in pod_data.items():
                data_to_send.append(ZabbixMetric(
                    self.zabbix_host, 'check_kubernetesd[get,pods,%s,%s,ready]' % (pods_namespace, container_name),
                    collect[pods_namespace][container_name]["ready"],
                ))
                data_to_send.append(ZabbixMetric(
                    self.zabbix_host, 'check_kubernetesd[get,pods,%s,%s,not_ready]' % (pods_namespace, container_name),
                    collect[pods_namespace][container_name]["not_ready"],
                ))
                data_to_send.append(ZabbixMetric(
                    self.zabbix_host, 'check_kubernetesd[get,pods,%s,%s,restart_count]' % (pods_namespace, container_name),
                    collect[pods_namespace][container_name]["restart_count"],
                ))

        return data_to_send

    def get_components(self):
        data_to_send = list()
        for component in self.data.get('components').get('items'):
            component_name = component['metadata']['name']
            failed_conds = []
            for cond in [x for x in component['conditions'] if x['type'].lower() == "healthy"]:
                if cond['status'] != 'True':
                    failed_conds.append(cond['type'])

            data_to_send.append(ZabbixMetric(
                self.zabbix_host, 'check_kubernetesd[get,components,' + component_name + ',available_status]',
                failed_conds if len(failed_conds) > 0 else 'OK')
            )
        return data_to_send

    def get_tls(self):
        data_to_send = list()
        for tls_cert in self.data.get('tls').get('items'):
            tls_name = tls_cert['metadata']['name']
            tls_namespace = tls_cert['metadata']['namespace']

            if 'data' not in tls_cert or not tls_cert['data']:
                self.logger.debug('No data for tls_cert "' + tls_namespace + '/' + tls_name + '"', tls_cert)
                continue

            if "tls.crt" not in tls_cert["data"]:
                continue

            base64_decode = base64.b64decode(tls_cert["data"]["tls.crt"])
            cert = x509.load_pem_x509_certificate(base64_decode, default_backend())
            expire_in = cert.not_valid_after - datetime.now()
            data_to_send.append(ZabbixMetric(
                self.zabbix_host, 'check_kubernetesd[get,tls,' + tls_namespace + ',' + tls_name + ',valid_days]',
                expire_in.days)
            )
        return data_to_send
