import re
import sys
import json
import base64
import logging

import time
import threading

from pyzabbix import ZabbixAPI, ZabbixMetric, ZabbixSender, ZabbixResponse, ZabbixAPIException
from kubernetes import client, config, watch
from kubernetes.client.rest import ApiException
from datetime import datetime, date, timedelta
from cryptography import x509
from cryptography.hazmat.backends import default_backend

from modules.timed_threads import TimedThread
from modules.watcher_thread import WatcherThread
from k8sobjects import K8sResourceManager, Pod, Deployment

exit_flag = threading.Event()


class DryResult:
    pass


def get_data_timeout_datetime():
    return datetime.now() - timedelta(minutes=1)


def get_discovery_timeout_datetime():
    return datetime.now() - timedelta(hours=1)


class KubernetesApi:
    __shared_state = dict(core_v1=None,
                          apps_v1=None)

    def __init__(self, api_client):
        self.__dict__ = self.__shared_state

        if not getattr(self, 'core_v1', None):
            self.core_v1 = client.CoreV1Api(api_client)
        if not getattr(self, 'apps_v1', None):
            self.apps_v1 = client.AppsV1Api(api_client)


data_refreshed = dict(api=datetime.now() - timedelta(hours=1),
                      discovery=dict(),
                      data=dict())


class CheckKubernetesDaemon:
    def __init__(self, config, config_name, resources, discovery_interval, data_interval):
        self.dirty_threads = False
        self.manage_threads = []
        self.data = {}

        self.logger = logging.getLogger(self.__class__.__name__)
        self.config_name = config_name
        self.discovery_interval = discovery_interval
        self.data_interval = data_interval

        self.api_configuration = client.Configuration()
        self.api_configuration.host = config.k8s_api_host
        self.api_configuration.verify_ssl = config.verify_ssl
        self.api_configuration.api_key = {"authorization": "Bearer " + config.k8s_api_token}

        self.api_client = client.ApiClient(self.api_configuration)
        self.core_v1 = KubernetesApi(self.api_client).core_v1
        self.apps_v1 = KubernetesApi(self.api_client).apps_v1
        self.zabbix_sender = ZabbixSender(zabbix_server=config.zabbix_server)
        self.zabbix_host = config.zabbix_host
        self.zabbix_debug = config.zabbix_debug
        self.zabbix_dry_run = config.zabbix_dry_run

        self.web_api_enable = config.web_api_enable
        self.web_api_host = config.web_api_host
        self.web_api_token = config.web_api_token
        self.web_api_cluster = config.web_api_cluster
        self.web_api_verify_ssl = config.web_api_verify_ssl

        self.resources = resources

        init_msg = "INIT K8S-ZABBIX Watcher\n<===>\n" \
                   "K8S API Server: %s\n" \
                   "Zabbix Server: %s\n" \
                   "Zabbix Host: %s\n" \
                   "Resources watching: %s\n" \
                   "web_api_enable => %s\n" \
                   "<===>" \
                   % (self.api_configuration.host, config.zabbix_server, self.zabbix_host, ",".join(self.resources), self.web_api_enable)
        self.logger.info(init_msg)

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
        for thread in self.manage_threads:
            thread.join(timeout=3)
        self.logger.info('All threads exited... exit check_kubernetesd')
        sys.exit(0)

    def run(self):
        self.start_watcher_threads()

    def start_watcher_threads(self):
        for resource in self.resources:
            self.data.setdefault(resource, K8sResourceManager(resource))

            thread = WatcherThread(resource, exit_flag,
                                   daemon=self, daemon_method='watch_data',
                                   discovery=True)
            self.manage_threads.append(thread)
            thread.start()

    def restart_dirty_threads(self):
        found_thread = None
        for thread in self.manage_threads:
            if thread.restart_thread:
                found_thread = thread

        if found_thread:
            self.logger.info('thread must be restarted: %s' % found_thread)

            resource = found_thread.resource
            found_thread.join()

            self.manage_threads = [x for x in self.manage_threads if x.resource != resource]
            thread = WatcherThread(resource, exit_flag,
                                   daemon=self, daemon_method='watch_data')
            self.manage_threads.append(thread)
            thread.start()
            del found_thread

    def get_api_for_resource(self, resource):
        if resource in ['nodes', 'components', 'tls', 'pods', 'services']:
            # use core_v1
            api = self.core_v1
        elif resource in ['deployments']:
            # use apps_v1
            api = self.apps_v1
        else:
            raise AttributeError('No valid resource found: %s' % resource)
        return api

    def get_web_api(self):
        if not hasattr(self, '_web_api'):
            from .web_api import WebApi
            self._web_api = WebApi(self.web_api_host, self.web_api_token, verify_ssl=self.web_api_verify_ssl)
        return self._web_api

    def watch_data(self, resource, discovery=False):
        api = self.get_api_for_resource(resource)

        w = watch.Watch()
        if resource == 'nodes':
            for s in w.stream(api.list_node):
                self.watch_event_handler(resource, s, discovery=discovery)
        elif resource == 'deployments':
            for s in w.stream(api.list_deployment_for_all_namespaces):
                self.watch_event_handler(resource, s, discovery=discovery)
        elif resource == 'components':
            # not supported
            pass
            # for s in w.stream(api.list_component_status, _request_timeout=60):
            #     self.watch_event_handler(resource, s)
        # elif resource == 'tls':
        #     return api.list_secret_for_all_namespaces(watch=False).to_dict()
        # elif resource == 'pods':
        #     for event in w.stream(api.list_pod_for_all_namespaces, _request_timeout=60):
        #         print(event)
        # elif resource == 'services':
        #     return api.list_service_for_all_namespaces(watch=False).to_dict()

    def watch_event_handler(self, resource, event, discovery=False):
        event_type = event['type']
        obj = event['object'].to_dict()
        # self.logger.debug(event_type + ': ' + obj['metadata']['name'])

        if event_type == 'ADDED':
            resourced_obj = self.data[resource].add_obj(obj)
            if resourced_obj.is_dirty:
                self.send_object(resource, resourced_obj, event_type)

    def send_object(self, resource, resourced_obj, event_type):
        """ send object with resourced values, set dirty flag """
        self.send_discovery_to_zabbix(resource, resourced_obj)
        self.send_to_web_api(resource, resourced_obj, event_type)
        resourced_obj.is_dirty = False

    @staticmethod
    def transform_value(value):
        if value is None:
            return 0
        m = re.match(r"^(\d+)Ki$", str(value))
        if m:
            return int(m.group(1)) * 1024
        return value

    def send_to_zabbix(self, metrics):
        if self.zabbix_dry_run:
            self.logger.info('send to zabbix: %s' % metrics)
            result = DryResult()
            result.failed = 0
        else:
            result = self.zabbix_sender.send(metrics)
        return result

    def send_discovery_to_zabbix(self, resource, obj):
        obj_name = obj.data['metadata']['name']
        global data_refreshed

        # initial
        metrics = list()
        if resource not in data_refreshed['discovery']:
            data_refreshed['discovery'][resource] = dict()
        if obj_name not in data_refreshed['discovery'][resource]:
            data_refreshed['discovery'][resource][obj_name] = datetime.now() - timedelta(hours=1)

        # discovery
        if data_refreshed['discovery'][resource][obj_name] < get_discovery_timeout_datetime():
            data = json.dumps({"data": {"{#NAME}": obj_name}})
            metrics.append(ZabbixMetric(self.zabbix_host, 'check_kubernetesd[discover,' + resource + ']', data))
            data_refreshed['discovery'][resource][obj_name] = datetime.now()

        if data_refreshed['api'] < get_data_timeout_datetime():
            metrics.append(ZabbixMetric(self.zabbix_host, 'check_kubernetesd[discover,api]', int(time.time())))
            data_refreshed['api'] = datetime.now()

        if metrics:
            result = self.send_to_zabbix(metrics)
            if result.failed > 0:
                self.logger.error("failed to sent discoveries: %s" % metrics)
            else:
                self.logger.info("successfully sent discoveries: %s" % metrics)

    def send_data_to_zabbix(self, resource, obj):
        global data_refreshed
        obj_name = obj.data['metadata']['name']

        if resource not in data_refreshed['data']:
            data_refreshed['data'][resource] = dict()

        metrics = list()
        for resource in self.resources:
            data_to_send = getattr(self, 'get_' + resource)()
            metrics += data_to_send
        metrics.append(ZabbixMetric(self.zabbix_host, 'check_kubernetesd[get,items]', int(time.time())))

        if self.zabbix_debug:
            for metric in metrics:
                result = self.send_to_zabbix([metric])
                if result.failed > 0:
                    self.logger.error("failed to sent items: %s", metric)
        else:
            result = self.send_to_zabbix(metrics)
            if result.failed > 0:
                self.logger.error("failed to sent %s items of %s items" % (result.failed, result.processed))
            else:
                self.logger.info("successfully sent %s items" % len(metrics))

    def send_to_web_api(self, resource, obj, action):
        if self.web_api_enable:
            api = self.get_web_api()
            data_to_send = self.get_data_for_resource(resource, obj)
            data_to_send['cluster'] = self.web_api_cluster
            api.send_data(resource, data_to_send, action)

    def get_data_for_resource(self, resource, obj):
        d = dict(
            name=obj.data['metadata']['name'],
            name_space=obj.data['metadata']['namespace'],
        )

        if resource == 'deployments':
            d.update(self.get_data_for_resource_deployment(obj))
        return d

    def get_data_for_resource_deployment(self, obj):
        d = dict()
        for status_type in obj.data['status']:
            if status_type == 'conditions':
                continue
            d.update({status_type: CheckKubernetesDaemon.transform_value(obj.data['status'][status_type])})

        failed_conds = []
        for cond in [x for x in obj.data['status']['conditions'] if x['type'].lower() == "available"]:
            if cond['status'] != 'True':
                failed_conds.append(cond['type'])
        d.update({'failed cons': failed_conds})
        return d

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
                    CheckKubernetesDaemon.transform_value(current_indirection))
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
                    CheckKubernetesDaemon.transform_value(deployment['status'][status_type]))
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
                            "status": "OK",
                        }
                    )
                    collect[pods_namespace][container_name]["restart_count"] += container['restart_count']

                    if container['ready'] is True:
                        collect[pods_namespace][container_name]["ready"] += 1
                    else:
                        collect[pods_namespace][container_name]["not_ready"] += 1

                    if container["state"] and len(container["state"]) > 0:
                        status_values = []
                        for status, data in container["state"].items():
                            if data and status != "running":
                                status_values.append(status)

                        if len(status_values) > 0:
                            collect[pods_namespace][container_name]["status"] = "ERROR: " + (",".join(status_values))

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
                data_to_send.append(ZabbixMetric(
                    self.zabbix_host, 'check_kubernetesd[get,pods,%s,%s,status]' % (pods_namespace, container_name),
                    collect[pods_namespace][container_name]["status"],
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
