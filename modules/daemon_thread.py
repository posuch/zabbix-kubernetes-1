import sys
import logging
import signal
import time
import json
import threading

from pyzabbix import ZabbixAPI, ZabbixMetric, ZabbixSender, ZabbixResponse, ZabbixAPIException
from kubernetes import client, config, watch
from kubernetes.client.rest import ApiException
from datetime import datetime, date, timedelta
from cryptography import x509
from cryptography.hazmat.backends import default_backend

from modules.timed_threads import TimedThread
from modules.watcher_thread import WatcherThread
from k8sobjects.k8sobject import K8sResourceManager, K8S_RESOURCES
from k8sobjects.container import get_container_zabbix_metrics

DELAY_DISCOVERY = 15

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
    data = {'zabbix_discovery_sent': {}}
    thread_lock = threading.RLock()

    def __init__(self, config, config_name,
                 resources, resources_excluded, resources_excluded_web, resources_excluded_zabbix,
                 discovery_interval, data_interval):
        self.dirty_threads = False
        self.manage_threads = []

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
        self.debug_k8s_events = False
        self.api_client = client.ApiClient(self.api_configuration)
        self.core_v1 = KubernetesApi(self.api_client).core_v1
        self.apps_v1 = KubernetesApi(self.api_client).apps_v1
        self.extensions_v1 = KubernetesApi(self.api_client).extensions_v1

        self.zabbix_sender = ZabbixSender(zabbix_server=config.zabbix_server)
        self.zabbix_resources = CheckKubernetesDaemon.exclude_resources(resources, resources_excluded_zabbix)
        self.zabbix_host = config.zabbix_host
        self.zabbix_debug = config.zabbix_debug
        self.zabbix_single_debug = config.zabbix_single_debug
        self.zabbix_dry_run = config.zabbix_dry_run

        self.web_api_enable = config.web_api_enable
        self.web_api_resources = CheckKubernetesDaemon.exclude_resources(resources, resources_excluded_web)

        self.web_api_host = config.web_api_host
        self.web_api_token = config.web_api_token
        self.web_api_cluster = config.web_api_cluster
        self.web_api_verify_ssl = config.web_api_verify_ssl

        self.resources = CheckKubernetesDaemon.exclude_resources(resources, resources_excluded)

        init_msg = "INIT K8S-ZABBIX Watcher\n<===>\n" \
                   "K8S API Server: %s\n" \
                   "Zabbix Server: %s\n" \
                   "Zabbix Host: %s\n" \
                   "Resources watching: %s\n" \
                   "web_api_enable => %s (resources: %s)\n" \
                   "web_api_host => %s\n" \
                   "<===>" \
                   % (self.api_configuration.host, config.zabbix_server, self.zabbix_host, ",".join(self.resources),
                      self.web_api_enable, ",".join(self.web_api_resources), self.web_api_host)
        self.logger.info(init_msg)

    @staticmethod
    def exclude_resources(available_types, excluded_types):
        result = []
        for k8s_type_available in available_types:
            if k8s_type_available not in excluded_types:
                result.append(k8s_type_available)
        return result

    def handler(self, signum, *args):
        if signum in [signal.SIGTERM, signal.SIGKILL]:
            self.logger.info('Signal handler called with signal %s... stopping (max %s seconds)' % (signum, 3))
            exit_flag.set()
            for thread in self.manage_threads:
                thread.join(timeout=3)
            self.logger.info('All threads exited... exit check_kubernetesd')
            sys.exit(0)
        elif signum in [signal.SIGUSR1]:
            self.logger.info('=== Listing count of data hold in CheckKubernetesDaemon.data ===')
            with self.thread_lock:
                for r, d in self.data.items():
                    rd = dict()
                    if hasattr(d, 'objects'):
                        for obj_name, obj_d in d.objects.items():
                            rd[obj_name] = dict(
                                last_sent_zabbix=obj_d.last_sent_zabbix,
                                last_sent_web=obj_d.last_sent_web,
                            )
                    else:
                        rd = d
                    self.logger.info('%s: %s' % (r, rd))
        elif signum in [signal.SIGUSR2]:
            self.logger.info('=== Listing all data hold in CheckKubernetesDaemon.data ===')
            with self.thread_lock:
                for r, d in self.data.items():
                    rd = dict()
                    if hasattr(d, 'objects'):
                        for obj_uid, obj in d.objects.items():
                            rd[obj_uid] = obj.data
                    else:
                        rd = d
                    self.logger.info('%s: %s\n' % (r, rd))

    def run(self):
        self.start_data_threads()
        self.start_api_info_threads()
        self.start_loop_send_discovery_threads()
        self.start_resend_threads()

    def start_data_threads(self):
        for resource in self.resources:
            with self.thread_lock:
                self.data.setdefault(resource, K8sResourceManager(resource, zabbix_host=self.zabbix_host))
                if resource == 'pods':
                    self.data.setdefault('containers', K8sResourceManager('containers'))

            # watcher threads
            if resource == 'containers':
                pass
            elif resource == 'components':
                thread = TimedThread(resource, self.data_interval, exit_flag,
                                     daemon=self, daemon_method='watch_data')
                self.manage_threads.append(thread)
                thread.start()
            else:
                thread = WatcherThread(resource, exit_flag,
                                       daemon=self, daemon_method='watch_data')
                self.manage_threads.append(thread)
                thread.start()

            # additional looping data threads
            if resource == 'services':
                thread = TimedThread(resource, self.data_interval, exit_flag,
                                     daemon=self, daemon_method='report_global_data_zabbix',
                                     start_delay=self.discovery_interval + 5)
                self.manage_threads.append(thread)
                thread.start()
            elif resource == 'containers':
                thread = TimedThread(resource, self.data_interval, exit_flag,
                                     daemon=self, daemon_method='report_global_data_zabbix',
                                     start_delay=self.discovery_interval + 5)
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
                                                daemon=self, daemon_method='send_discovery', start_delay=DELAY_DISCOVERY)
            self.manage_threads.append(send_discovery_thread)
            send_discovery_thread.start()

    def start_resend_threads(self):
        if 'nodes' in self.resources:
            rate_limit_resend_thread = TimedThread('resend_dirty_thread', self.rate_limit_resend_interval, exit_flag,
                                                   daemon=self, daemon_method='resend_dirty_and_rate_limited')
            self.manage_threads.append(rate_limit_resend_thread)
            rate_limit_resend_thread.start()

        for resource in self.resources:
            data_resend_thread = TimedThread(resource, self.data_interval, exit_flag,
                                             daemon=self, daemon_method='resend_data', start_delay=DELAY_DISCOVERY + 2)
            self.manage_threads.append(data_resend_thread)
            data_resend_thread.start()

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
        if resource in ['nodes', 'components', 'secrets', 'pods', 'services']:
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

    def watch_data(self, resource, send_zabbix_discovery=False, timeout=240):
        api = self.get_api_for_resource(resource)

        if timeout == 0:
            timeout_str = "no timeout"
        else:
            timeout_str = "%i seconds" % timeout

        self.logger.info("Watching for resource >>>%s<<< with a timeout of %s" % (resource, timeout_str))
        while True:
            w = watch.Watch()
            if resource == 'nodes':
                for obj in w.stream(api.list_node, timeout_seconds=timeout):
                    self.watch_event_handler(resource, obj)
            elif resource == 'deployments':
                for obj in w.stream(api.list_deployment_for_all_namespaces, timeout_seconds=timeout):
                    self.watch_event_handler(resource, obj)
            elif resource == 'daemonsets':
                for obj in w.stream(api.list_daemon_set_for_all_namespaces, timeout_seconds=timeout):
                    self.watch_event_handler(resource, obj)
            elif resource == 'statefulsets':
                for obj in w.stream(api.list_stateful_set_for_all_namespaces, timeout_seconds=timeout):
                    self.watch_event_handler(resource, obj)
            elif resource == 'components':
                # The api does not support watching on component status
                for obj in api.list_component_status(watch=False).to_dict().get('items'):
                    self.data[resource].add_obj(obj)
                time.sleep(self.data_interval)
            elif resource == 'ingresses':
                for obj in w.stream(api.list_ingress_for_all_namespaces, timeout_seconds=timeout):
                    self.watch_event_handler(resource, obj)
            elif resource == 'tls':
                for obj in w.stream(api.list_secret_for_all_namespaces, timeout_seconds=timeout):
                    self.watch_event_handler(resource, obj)
            elif resource == 'pods':
                for obj in w.stream(api.list_pod_for_all_namespaces, timeout_seconds=timeout):
                    self.watch_event_handler(resource, obj)
            elif resource == 'services':
                for obj in w.stream(api.list_service_for_all_namespaces, timeout_seconds=timeout):
                    self.watch_event_handler(resource, obj)
            else:
                self.logger.error("No watch handling for resource %s" % resource)
                time.sleep(60)
            self.logger.debug("Watch/fetch completed for resource >>>%s<<<, restarting" % resource)

    def watch_event_handler(self, resource, event):
        event_type = event['type']
        obj = event['object'].to_dict()
        self.logger.debug(event_type + ' [' + resource + ']: ' + obj['metadata']['name'])
        if not self.data[resource].resource_class:
            self.logger.error('Could not add watch_event_handler! No resource_class for "%s"' % resource)
            return

        if event_type.lower() == 'added':
            with self.thread_lock:
                resourced_obj = self.data[resource].add_obj(obj)
                if resourced_obj.is_dirty_zabbix or resourced_obj.is_dirty_web:
                    self.send_object(resource, resourced_obj, event_type,
                                     send_zabbix_discovery=resourced_obj.last_sent_zabbix_discovery == 0,
                                     send_zabbix_data=resourced_obj.is_dirty_zabbix,
                                     send_web=resourced_obj.is_dirty_web)
        elif event_type.lower() == 'modified':
            with self.thread_lock:
                resourced_obj = self.data[resource].add_obj(obj)
                if resourced_obj.is_dirty_zabbix or resourced_obj.is_dirty_web:
                    self.send_object(resource, resourced_obj, event_type,
                                     send_zabbix_data=resourced_obj.is_dirty_zabbix,
                                     send_web=resourced_obj.is_dirty_web)
        elif event_type.lower() == 'deleted':
            with self.thread_lock:
                resourced_obj = self.data[resource].del_obj(obj)
                self.delete_object(resource, resourced_obj)
                self.data[resource].delete_obj(obj)
        else:
            self.logger.info('event type "%s" not watched' % event_type)

    def report_global_data_zabbix(self, resource):
        """ aggregate and report information for some speciality in resources """
        if self.data['zabbix_discovery_sent'].get(resource) is None:
            self.logger.debug('skipping report_global_data_zabbix for %s, disovery not send yet!' % resource)
            return

        data_to_send = list()

        if resource == 'services':
            num_services = 0
            num_ingress_services = 0
            with self.thread_lock:
                for obj_uid, resourced_obj in self.data[resource].objects.items():
                    num_services += 1
                    if resourced_obj.resource_data['is_ingress']:
                        num_ingress_services += 1

            data_to_send.append(ZabbixMetric(self.zabbix_host, 'check_kubernetes[get,services,num_services]', num_services))
            data_to_send.append(ZabbixMetric(self.zabbix_host, 'check_kubernetes[get,services,num_ingress_services]', num_ingress_services))
            self.send_data_to_zabbix(resource, None, data_to_send)
        elif resource == 'containers':
            # aggregate pod data to containers for each namespace
            with self.thread_lock:
                containers = dict()
                for obj_uid, resourced_obj in self.data['pods'].objects.items():
                    ns = resourced_obj.name_space
                    if ns not in containers:
                        containers[ns] = dict()

                    pod_data = resourced_obj.resource_data
                    pod_base_name = resourced_obj.base_name
                    for container_name, container_data in pod_data['container_status'].items():
                        containers[ns].setdefault(pod_base_name, dict())
                        containers[ns][pod_base_name].setdefault(container_name, container_data)
                        for k, v in containers[ns][pod_base_name][container_name].items():
                            if isinstance(v, int):
                                containers[ns][container_name][pod_base_name][k] += container_data[k]
                            elif k == 'status' and container_data[k].startswith('ERROR'):
                                containers[ns][container_name][pod_base_name][k] = container_data[k]

                for ns, d1 in containers.items():
                    for pod_base_name, d2 in d1.items():
                        for container_name, container_data in d2.items():
                            data_to_send += get_container_zabbix_metrics(self.zabbix_host, ns, pod_base_name, container_name, container_data)

                self.send_data_to_zabbix(resource, None, data_to_send)

    def resend_data(self, resource):
        if resource not in self.data or len(self.data[resource].objects) == 0:
            return

        metrics = list()

        for obj_uid, obj in self.data[resource].objects.items():
            if obj.last_sent_zabbix == 0 or \
                    (obj.last_sent_zabbix < (datetime.now() - timedelta(seconds=self.data_interval))):
                metrics += obj.get_zabbix_metrics()
                obj.last_sent_zabbix = datetime.now()
        if len(metrics) > 0:
            self.logger.info('resending data for %s' % resource)
            self.send_data_to_zabbix(resource, metrics=metrics)

    def resend_dirty_and_rate_limited(self, resource_unused):
        # resend for all resources

        with self.thread_lock:
            try:
                for resource in K8S_RESOURCES.keys():
                    if self.data['zabbix_discovery_sent'].get(resource) is None:
                        self.logger.debug('skipping resend_dirty_and_rate_limited, discovery for %s not sent yet!' % resource)
                        continue

                    metrics = list()
                    if resource not in self.data or len(self.data[resource].objects) == 0:
                        continue

                    for obj_uid, obj in self.data[resource].objects.items():
                        if obj.is_dirty_zabbix or obj.is_dirty_web:
                            if obj.is_dirty_web:
                                self.send_to_web_api(resource, obj, 'ADDED' if obj.last_sent_web == 0 else 'MODIFIED')
                                obj.is_dirty_web = False
                            if obj.is_dirty_zabbix:
                                metrics += obj.get_zabbix_metrics()
                                obj.last_sent_zabbix = datetime.now()
                                obj.is_dirty_zabbix = False

                        # elif obj.last_sent_web == 0 or \
                        #      (obj.last_sent_web != 0 and
                        #       obj.last_sent_web < datetime.now() - timedelta(seconds=self.data_interval)):
                        #
                        #     self.send_to_web_api(resource, obj, 'MODIFIED')
                    if len(metrics) > 0:
                        self.logger.debug('resend dirty data for %s' % resource)
                        self.send_data_to_zabbix(resource, metrics=metrics)
            except RuntimeError as e:
                self.logger.warning(str(e))

    def delete_object(self, resource, resourced_obj):
        pass

    def send_discovery(self, resource):
        with self.thread_lock:
            if resource in self.data and len(self.data[resource].objects) > 0:
                data = list()
                for obj_uid, obj in self.data[resource].objects.items():
                    data += obj.get_zabbix_discovery_data()

                if data:
                    metric = obj.get_discovery_for_zabbix(data)
                    self.logger.debug('sending discovery for [%s]: %s' % (resource, metric))
                    self.send_discovery_to_zabbix(resource, metric=[metric])
            self.data['zabbix_discovery_sent'][resource] = datetime.now()

    def send_object(self, resource, resourced_obj, event_type, send_zabbix_discovery=False, send_zabbix_data=False, send_web=False):
        # send single object for updates
        with self.thread_lock:

            if send_zabbix_discovery and self.data['zabbix_discovery_sent'].get(resource) is not None:
                self.send_discovery_to_zabbix(resource, obj=resourced_obj)

            if send_zabbix_data and self.data['zabbix_discovery_sent'].get(resource) is not None:
                if resourced_obj.last_sent_zabbix == 0 or resourced_obj.last_sent_zabbix < datetime.now() - timedelta(seconds=10):
                    self.send_data_to_zabbix(resource, obj=resourced_obj)
                    resourced_obj.last_sent_zabbix = datetime.now()
                    resourced_obj.is_dirty_zabbix = False
                else:
                    self.logger.info('obj >>>type: %s, name: %s<<< not sending to zabbix! rate limited (10s)' % (resource, resourced_obj.name))

            if send_web:
                if resourced_obj.last_sent_web == 0 or resourced_obj.last_sent_web < datetime.now() - timedelta(seconds=10):
                    self.send_to_web_api(resource, resourced_obj, event_type)
                    resourced_obj.last_sent_web = datetime.now()
                    if resourced_obj.is_dirty_web is True and not send_zabbix_data:
                        # only set dirty False if send_zabbix_data worked
                        resourced_obj.is_dirty_web = False
                else:
                    self.logger.info('obj >>>type: %s, name: %s<<< not sending to web! rate limited (10s)' % (resource, resourced_obj.name))

    def send_api_info(self, *args):
        result = self.send_to_zabbix([
            ZabbixMetric(self.zabbix_host, 'check_kubernetesd[discover,api]', int(time.time()))
        ])
        if result.failed > 0:
            self.logger.error("failed to send heartbeat")
        else:
            self.logger.debug("successfully sent heartbeat")

    def send_to_zabbix(self, metrics):
        if self.zabbix_dry_run:
            result = DryResult()
            result.failed = 0
            if self.debug_k8s_events:
                self.logger.debug('===> Sending to zabbix: %s\n' % metrics)
        else:
            try:
                result = self.zabbix_sender.send(metrics)
            except Exception as e:
                self.logger.error(e)
                result = DryResult()
                result.failed = 1
        return result

    def send_discovery_to_zabbix(self, resource, metric=None, obj=None):
        if resource not in self.zabbix_resources:
            return

        if obj:
            with self.thread_lock:
                discovery_data = obj.get_discovery_for_zabbix()
            if not discovery_data:
                self.logger.debug('No discovery_data for obj %s, not sending to zabbix!' % obj.uid)
                return

            discovery_key = 'check_kubernetesd[discover,' + resource + ']'
            result = self.send_to_zabbix([ZabbixMetric(self.zabbix_host, discovery_key, discovery_data)])
            if result.failed > 0:
                self.logger.error("failed to sent discovery: %s : >>>%s<<<" % (discovery_key, discovery_data))
            elif self.zabbix_debug:
                self.logger.info("successfully sent discovery: %s  >>>>%s<<<" % (discovery_key, discovery_data))
        elif metric:
            result = self.send_to_zabbix(metric)

            if result.failed > 0:
                self.logger.error("failed to sent mass discovery: >>>%s<<<" % metric)
            elif self.zabbix_debug:
                self.logger.info("successfully sent mass discovery: >>>%s<<<" % metric)
        else:
            self.logger.warning('No obj or metrics found for send_discovery_to_zabbix [%s]' % resource)

    def send_data_to_zabbix(self, resource, obj=None, metrics=[]):
        if resource not in self.zabbix_resources:
            return

        if obj and len(metrics) == 0:
            metrics = obj.get_zabbix_metrics()

        if len(metrics) == 0 and obj:
            self.logger.debug('No metrics to send for %s: %s' % (obj.uid, metrics))
            return
        elif len(metrics) == 0:
            self.logger.debug('No metrics or no obj found for [%s]' % resource)
            return

        if self.zabbix_single_debug:
            for metric in metrics:
                result = self.send_to_zabbix([metric])
                if result.failed > 0:
                    self.logger.error("failed to sent data items: %s", metric)
                else:
                    self.logger.info("successfully sent data items: %s", metric)
        else:
            result = self.send_to_zabbix(metrics)
            if result.failed > 0:
                self.logger.error("failed to sent %s items, processed %s items [%s: %s]"
                                  % (result.failed, result.processed, resource, obj.name if obj else 'metrics'))
                self.logger.debug(metrics)
            else:
                self.logger.debug("successfully sent %s items [%s: %s]" % (len(metrics), resource, obj.name if obj else 'metrics'))

    def send_to_web_api(self, resource, obj, action):
        if self.web_api_enable and resource in self.web_api_resources:
            api = self.get_web_api()
            with self.thread_lock:
                data_to_send = obj.resource_data
            data_to_send['cluster'] = self.web_api_cluster
            api.send_data(resource, data_to_send, action)
            obj.last_sent_web = datetime.now()
