import re
import sys
import json
import base64
import logging
import signal
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
from k8sobjects.k8sobject import K8sResourceManager

exit_flag = threading.Event()


class DryResult:
    pass


def get_data_timeout_datetime():
    return datetime.now() - timedelta(minutes=1)


def get_discovery_timeout_datetime():
    return datetime.now() - timedelta(hours=1)


class KubernetesApi:
    __shared_state = dict(core_v1=None,
                          apps_v1=None,
                          extensions_v1=None)

    def __init__(self, api_client):
        self.__dict__ = self.__shared_state

        if not getattr(self, 'core_v1', None):
            self.core_v1 = client.CoreV1Api(api_client)
        if not getattr(self, 'apps_v1', None):
            self.apps_v1 = client.AppsV1Api(api_client)
        if not getattr(self, 'exentsions_v1', None):
            self.extensions_v1 = client.ExtensionsV1beta1Api(api_client)


class CheckKubernetesDaemon:
    def __init__(self, config, config_name, resources, discovery_interval, data_interval):
        self.dirty_threads = False
        self.manage_threads = []
        self.data = {}

        self.logger = logging.getLogger(self.__class__.__name__)
        self.config_name = config_name
        self.discovery_interval = discovery_interval
        self.data_interval = data_interval

        self.api_zabbix_interval = 60
        self.rate_limit_resend_interval = 10
        self.api_configuration = client.Configuration()
        self.api_configuration.host = config.k8s_api_host
        self.api_configuration.verify_ssl = config.verify_ssl
        self.api_configuration.api_key = {"authorization": "Bearer " + config.k8s_api_token}

        # K8S API
        self.api_client = client.ApiClient(self.api_configuration)
        self.core_v1 = KubernetesApi(self.api_client).core_v1
        self.apps_v1 = KubernetesApi(self.api_client).apps_v1
        self.extensions_v1 = KubernetesApi(self.api_client).extensions_v1

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
                   "web_api_host => %s\n" \
                   "<===>" \
                   % (self.api_configuration.host, config.zabbix_server, self.zabbix_host, ",".join(self.resources),
                      self.web_api_enable, self.web_api_host)
        self.logger.info(init_msg)

    def handler(self, signum, *args):
        if signum in [signal.SIGTERM, signal.SIGKILL]:
            self.logger.info('Signal handler called with signal %s... stopping (max %s seconds)' % (signum, 3))
            exit_flag.set()
            for thread in self.manage_threads:
                thread.join(timeout=3)
            self.logger.info('All threads exited... exit check_kubernetesd')
            sys.exit(0)
        elif signum in [signal.SIGUSR1]:
            self.logger.info('=== Listing all data hold in daemon for %s ===' % self.resources)
            for r, d in self.data.items():
                rd = dict()
                for obj_uid, obj in d.objects.items():
                    rd[obj_uid] = obj.data
                self.logger.info('%s: %s' % (r, rd))

    def run(self):
        self.start_data_threads()
        self.start_api_info_threads()
        self.start_loop_send_discovery_threads()
        self.start_resend_threads()

    def start_data_threads(self):
        for resource in self.resources:
            self.data.setdefault(resource, K8sResourceManager(resource))

            if resource is 'components':
                thread = TimedThread(resource, self.data_interval, exit_flag,
                                     daemon=self, daemon_method='watch_data')
                self.manage_threads.append(thread)
                thread.start()
            else:
                thread = WatcherThread(resource, exit_flag,
                                       daemon=self, daemon_method='watch_data',
                                       send_discovery=True)
                self.manage_threads.append(thread)
                thread.start()

            # additional looping data threads
            if resource is 'services':
                thread = TimedThread(resource, self.data_interval, exit_flag,
                                     daemon=self, daemon_method='report_data', start_delay=5)
                self.manage_threads.append(thread)
                thread.start()

    def start_api_info_threads(self):
        if 'nodes' not in self.resources:
            # only send api heartbeat once
            return

        thread = TimedThread('api_info', self.api_zabbix_interval, exit_flag,
                             daemon=self, daemon_method='send_api_info')
        self.manage_threads.append(thread)
        thread.start()

    def start_loop_send_discovery_threads(self):
        for resource in self.resources:
            send_discovery_thread = TimedThread(resource, self.discovery_interval, exit_flag,
                                                daemon=self, daemon_method='send_discovery', delay=True)
            self.manage_threads.append(send_discovery_thread)
            send_discovery_thread.start()

    def start_resend_threads(self):
        rate_limit_resend_thread = TimedThread('resend_dirty_thread', self.rate_limit_resend_interval, exit_flag,
                                               daemon=self, daemon_method='resend_data_and_dirty_rate_limited', delay=True)
        self.manage_threads.append(rate_limit_resend_thread)
        rate_limit_resend_thread.start()

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

    def resend_data_and_dirty_rate_limited(self, resource_unused):
        try:
            for resource in self.resources:
                if resource in self.data and len(self.data[resource].objects) > 0:
                    for obj_uid, obj in self.data[resource].objects.items():
                        if obj.is_dirty:
                            self.send_object(resource, obj, 'MODIFIED')
                        elif obj.last_sent is 0 or \
                                (obj.last_sent is not 0 and obj.last_sent < datetime.now() - timedelta(seconds=self.data_interval)):
                            only_zabbix = False
                            if obj.last_sent is not 0:
                                # only send to zabbix (refresh not modified data if it was sent earlier)
                                only_zabbix = True
                            self.send_object(resource, obj, 'MODIFIED', only_zabbix=only_zabbix)

        except RuntimeError as e:
            self.logger.warning(str(e))

    def resend_resource_data(self, resource):
        try:
            if resource in self.data and len(self.data[resource].objects) > 0:
                for obj_uid, obj in self.data[resource].objects.items():
                    if obj.last_sent is not 0 and obj.last_sent > datetime.now() - timedelta(seconds=self.data_interval):
                        continue
                    self.send_object(resource, obj, 'MODIFIED')
        except RuntimeError as e:
            self.logger.warning(str(e))

    def get_api_for_resource(self, resource):
        if resource in ['nodes', 'components', 'tls', 'pods', 'services']:
            api = self.core_v1
        elif resource in ['deployments', 'daemonsets', 'statefulsets']:
            api = self.apps_v1
        elif resource in ['ingresses']:
            api = self.extensions_v1
        else:
            raise AttributeError('No valid resource found: %s' % resource)
        return api

    def get_web_api(self):
        if not hasattr(self, '_web_api'):
            from .web_api import WebApi
            self._web_api = WebApi(self.web_api_host, self.web_api_token, verify_ssl=self.web_api_verify_ssl)
        return self._web_api

    def watch_data(self, resource, send_discovery=False):
        api = self.get_api_for_resource(resource)

        w = watch.Watch()
        if resource == 'nodes':
            for obj in w.stream(api.list_node):
                self.watch_event_handler(resource, obj, send_discovery=send_discovery)
        elif resource == 'deployments':
            for obj in w.stream(api.list_deployment_for_all_namespaces):
                self.watch_event_handler(resource, obj, send_discovery=send_discovery)
        elif resource == 'daemonsets':
            for obj in w.stream(api.list_daemon_set_for_all_namespaces):
                self.watch_event_handler(resource, obj, send_discovery=send_discovery)
        elif resource == 'statefulsets':
            for obj in w.stream(api.list_stateful_set_for_all_namespaces):
                self.watch_event_handler(resource, obj, send_discovery=send_discovery)
        elif resource == 'components':
            for obj in api.list_component_status(watch=False).to_dict().get('items'):
                self.data[resource].add_obj(obj)
        elif resource == 'ingresses':
            for obj in w.stream(api.list_ingress_for_all_namespaces):
                self.watch_event_handler(resource, obj, send_discovery=send_discovery)
        elif resource == 'tls':
            for obj in w.stream(api.list_secret_for_all_namespaces):
                self.watch_event_handler(resource, obj, send_discovery=send_discovery)
        elif resource == 'pods':
            for obj in w.stream(api.list_pod_for_all_namespaces):
                self.watch_event_handler(resource, obj, send_discovery=send_discovery)
        elif resource == 'services':
            for obj in w.stream(api.list_service_for_all_namespaces):
                self.watch_event_handler(resource, obj, send_discovery=send_discovery)

    def watch_event_handler(self, resource, event, send_discovery=False):
        event_type = event['type']
        obj = event['object'].to_dict()
        # self.logger.debug(event_type + ': ' + obj['metadata']['name'])
        if not self.data[resource].resource_class:
            self.logger.error('Could not add watch_event_handler! No resource_class for "%s"' % resource)
            return

        if event_type.lower() in ['added', 'modified']:
            resourced_obj = self.data[resource].add_obj(obj)
            if resourced_obj.is_dirty:
                self.send_object(resource, resourced_obj, event_type, send_discovery=send_discovery)
        elif event_type.lower() == 'deleted':
            resourced_obj = self.data[resource].del_obj(obj)
            self.delete_object(resource, resourced_obj)
            self.data[resource].delete_obj(obj)
        else:
            self.logger.info('event type "%s" not watched' % event_type)

    def report_data(self, resource, send_discovery=False):
        data_to_send = list()
        if resource is 'services':
            num_services = 0
            num_ingress_services = 0
            for obj_uid, resourced_obj in self.data[resource].objects.items():
                num_services += 1
                if resourced_obj.resource_data['is_ingress']:
                    num_ingress_services += 1

            data_to_send.append(ZabbixMetric(self.zabbix_host, 'check_kubernetes[get,services,num_services]', num_services))
            data_to_send.append(ZabbixMetric(self.zabbix_host, 'check_kubernetes[get,services,num_ingress_services]', num_ingress_services))
            self.send_data_to_zabbix(resource, None, data_to_send)

    def send_object(self, resource, resourced_obj, event_type, send_discovery=False, only_zabbix=False):
        self.logger.debug('send obj %s (last_sent %s)' % (resourced_obj.name, resourced_obj.last_sent))
        if resourced_obj.last_sent is not 0 and resourced_obj.last_sent > datetime.now() - timedelta(seconds=10):
            self.logger.info('obj %s not sending! rate limited (10s)' % resourced_obj.name)
            return

        if send_discovery:
            self.send_discovery_to_zabbix(resource, resourced_obj)

        self.send_data_to_zabbix(resource, obj=resourced_obj)
        if not only_zabbix:
            self.send_to_web_api(resource, resourced_obj, event_type)

        resourced_obj.is_dirty = False
        resourced_obj.last_sent = datetime.now()

    def delete_object(self, resource, resourced_obj):
        pass

    def send_discovery(self, resource):
        if resource in self.data and len(self.data[resource].objects) > 0:
            self.logger.debug('sending discovery for %s [%s] (%s)'
                              % (resource, self.data[resource].objects.keys(), len(self.data[resource].objects)))
            for obj_uid, obj in self.data[resource].objects.items():
                self.send_object(resource, obj, 'ADDED')

    def send_api_info(self, *args):
        result = self.send_to_zabbix([
            ZabbixMetric(self.zabbix_host, 'check_kubernetesd[discover,api]', int(time.time()))
        ])
        if result.failed > 0:
            self.logger.error("failed to api info")
        else:
            self.logger.info("successfully sent api info")

    def send_to_zabbix(self, metrics):
        if self.zabbix_dry_run:
            result = DryResult()
            result.failed = 0
        else:
            result = self.zabbix_sender.send(metrics)
        return result

    def send_discovery_to_zabbix(self, resource, obj):
        discovery_data = obj.get_discovery_for_zabbix()
        if not discovery_data:
            self.logger.debug('No discovery_data for obj %s, not sending to zabbix!' % obj.uid)
            return

        result = self.send_to_zabbix([ZabbixMetric(self.zabbix_host, 'check_kubernetesd[discover,' + resource + ']', discovery_data)])
        if result.failed > 0:
            self.logger.error("failed to sent discoveries: %s" % obj.uid)
        elif self.zabbix_debug:
            self.logger.info("successfully sent discoveries: %s" % obj.uid)

    def send_data_to_zabbix(self, resource, obj=None, metrics=None):
        if obj and not metrics:
            metrics = obj.get_zabbix_metrics(self.zabbix_host)

        if not metrics:
            self.logger.debug('No metrics to send for %s: %s' % (obj.uid, metrics))
            return

        if self.zabbix_debug:
            for metric in metrics:
                result = self.send_to_zabbix([metric])
                if result.failed > 0:
                    self.logger.error("failed to sent data items: %s", metric)
                else:
                    self.logger.info("successfully sent data items: %s", metric)
        else:
            result = self.send_to_zabbix(metrics)
            if result.failed > 0:
                self.logger.error("failed to sent %s items, processed %s items [%s: %s)"
                                  % (result.failed, result.processed, resource, obj.name if obj else '-'))
                self.logger.debug(metrics)
            else:
                self.logger.debug("successfully sent %s items [%s: %s]" % (len(metrics), resource, obj.name if obj else '-'))

    def send_to_web_api(self, resource, obj, action):
        if self.web_api_enable:
            api = self.get_web_api()
            data_to_send = obj.resource_data
            data_to_send['cluster'] = self.web_api_cluster
            api.send_data(resource, data_to_send, action)
