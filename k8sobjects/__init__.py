import re
import datetime
import importlib
import hashlib
import json
import logging

from pyzabbix import ZabbixMetric

logger = logging.getLogger(__name__)


def json_encoder(obj):
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()


def get_k8s_class_identifier(resource):
    return dict(
        nodes='node',
        components='component',
        services='service',
        deployments='deployment',
        pods='pod',
        tls='tls',
    )[resource]


def transform_value(value):
    if value is None:
        return 0
    m = re.match(r"^(\d+)Ki$", str(value))
    if m:
        return int(m.group(1)) * 1024
    return value


class K8sResourceManager:
    def __init__(self, resource):
        self.objects = dict()
        self.resource = resource
        mod = importlib.import_module('k8sobjects')
        class_label = get_k8s_class_identifier(resource)

        self.resource_class = getattr(mod, class_label.capitalize(), None)

    def add_obj(self, obj):
        new_obj = self.resource_class(obj, self.resource)
        if new_obj.uid not in self.objects:
            # new object
            self.objects[new_obj.uid] = new_obj
        elif self.objects[new_obj.uid].data_checksum != new_obj.data_checksum:
            # existing object with modified data
            new_obj.last_sent = self.objects[new_obj.uid].last_sent
            new_obj.is_dirty = True
            self.objects[new_obj.uid] = new_obj

        # return created or updated object
        return self.objects[new_obj.uid]

    def del_obj(self, obj):
        deleted_obj = None
        if obj.uid in self.objects:
            deleted_obj = json.loads(
                json.dumps(
                    self.objects[obj.uid],
                    sort_keys=True,
                    default=json_encoder,
                )
            )
            del self.objects[obj.uid]
        return deleted_obj


class K8sObject:
    def __init__(self, obj_data, resource):
        self.is_dirty = True
        self.last_sent = 0
        self.resource = resource
        self.data = obj_data
        self.data_checksum = self.calculate_checksum()

    def __str__(self):
        return self.uid

    @property
    def resource_data(self):
        """ customized values for k8s objects """
        return dict(
            name=self.data['metadata']['name'],
            name_space=self.data['metadata']['namespace'],
        )

    @property
    def uid(self):
        if not hasattr(self, 'object_type'):
            raise AttributeError('No object_type set! Dont use K8sObject itself!')
        elif not self.name:
            raise AttributeError('No name set for K8sObject.uid! [%s] name_space: %s, name: %s'
                                 % (self.object_type, self.name_space, self.name))

        if self.name_space:
            return self.object_type + '_' + self.name_space + '_' + self.name
        return self.object_type + '_ ' + self.name

    @property
    def name(self):
        name = self.data.get('metadata', {}).get('name')
        if not name:
            logger.warning('Could not find name for obj %s' % self)
        return name

    @property
    def name_space(self):
        if isinstance(self, Node):
            return None

        name_space = self.data.get('metadata', {}).get('namespace')
        if not name_space:
            logger.warning('Could not find name_space for obj %s' % self)
        return name_space

    def calculate_checksum(self):
        return hashlib.md5(
            json.dumps(
                self.data,
                sort_keys=True,
                default=json_encoder,
            ).encode('utf-8')
        ).hexdigest()


class Node(K8sObject):
    object_type = 'node'

    MONITOR_VALUES = ['allocatable.cpu',
                      'allocatable.ephemeral-storage',
                      'allocatable.memory',
                      'allocatable.pods',
                      'capacity.cpu',
                      'capacity.ephemeral-storage',
                      'capacity.memory',
                      'capacity.pods']

    @property
    def resource_data(self):
        data = super().resource_data

        failed_conds = []
        data['condition_ready'] = False
        for cond in self.data['status']['conditions']:
            if cond['type'].lower() == "ready" and cond['status'] == 'True':
                data['condition_ready'] = True
            else:
                if cond['status'] == 'True':
                    failed_conds.append(cond['type'])

        data['failed_conds'] = failed_conds

        for monitor_value in self.MONITOR_VALUES:
            current_indirection = self.data['status']
            for key in monitor_value.split("."):
                current_indirection = current_indirection[key]

            data[monitor_value] = transform_value(current_indirection)

        return data

    def get_zabbix_metrics(self, zabbix_host):
        data_to_send = list()
        data = self.resource_data

        data_to_send.append(ZabbixMetric(zabbix_host, 'check_kubernetesd[get,nodes,' + self.name + ',available_status]',
                                         'not available' if data['condition_ready'] is not True else 'OK'))
        data_to_send.append(ZabbixMetric(zabbix_host, 'check_kubernetesd[get,nodes,' + self.name + ',condition_status_failed]',
                                         data['failed_conds'] if len(data['failed_conds']) > 0 else 'OK'))
        for monitor_value in self.MONITOR_VALUES:
            data_to_send.append(ZabbixMetric(
                zabbix_host, 'check_kubernetesd[get,nodes,%s,%s]' % (self.name, monitor_value),
                transform_value(data[monitor_value]))
            )
        return data_to_send


class Pod(K8sObject):
    object_type = 'pod'

    @property
    def resource_data(self):
        data = super().resource_data
        containers = {}
        for container in self.data['spec']['containers']:
            containers.setdefault(container['name'], 0)
            containers[container['name']] += 1
        data['containers'] = containers
        data['status'] = {}

        if "container_statuses" in self.data['status'] and self.data['status']['container_statuses']:
            for container in self.data['status']['container_statuses']:
                container_name = container['name']
                data['status'][container_name] = {
                    "restart_count": container['restart_count'],
                    "ready": 0,
                    "not_ready": 0,
                    "status": "OK",
                }

                if container['ready'] is True:
                    data['status'][container_name]["ready"] += 1
                else:
                    data['status'][container_name]["not_ready"] += 1

                if container["state"] and len(container["state"]) > 0:
                    status_values = []
                    for status, container_data in container["state"].items():
                        if container_data and status != "running":
                            status_values.append(status)

                    if len(status_values) > 0:
                        data['status'][container_name]["status"] = 'ERROR: ' + (','.join(status_values))

        return data

    def get_zabbix_metrics(self, zabbix_host):
        data = self.resource_data
        data_to_send = list()

        if 'status' not in data:
            logger.error(data)

        for container_name, data in data['status'].items():
            data_to_send.append(ZabbixMetric(
                zabbix_host, 'check_kubernetesd[get,pods,%s,%s,ready]' % (self.name_space, container_name),
                data["ready"],
            ))
            data_to_send.append(ZabbixMetric(
                zabbix_host, 'check_kubernetesd[get,pods,%s,%s,not_ready]' % (self.name_space, container_name),
                data["not_ready"],
            ))
            data_to_send.append(ZabbixMetric(
                zabbix_host, 'check_kubernetesd[get,pods,%s,%s,restart_count]' % (self.name_space, container_name),
                data["restart_count"],
            ))
            data_to_send.append(ZabbixMetric(
                zabbix_host, 'check_kubernetesd[get,pods,%s,%s,status]' % (self.name_space, container_name),
                data["status"],
            ))

        return data_to_send


class Deployment(K8sObject):
    object_type = 'deployment'

    @property
    def resource_data(self):
        data = super().resource_data

        for status_type in self.data['status']:
            if status_type == 'conditions':
                continue
            data.update({status_type: transform_value(self.data['status'][status_type])})

        failed_conds = []
        available_conds = [x for x in self.data['status']['conditions'] if x['type'].lower() == "available"]
        if available_conds:
            for cond in available_conds:
                if cond['status'] != 'True':
                    failed_conds.append(cond['type'])

        data['failed cons'] = failed_conds
        if len(failed_conds) > 0:
            data['status'] = 'ERROR: ' + (','.join(failed_conds))
        else:
            data['status'] = 'OK'

        return data

    def get_zabbix_metrics(self, zabbix_host):
        data = self.resource_data
        data_to_send = []

        for status_type in self.data['status']:
            if status_type == 'conditions':
                continue

            data_to_send.append(ZabbixMetric(
                zabbix_host, 'check_kubernetesd[get,deployments,%s,%s,%s]' % (self.name_space, self.name, status_type),
                transform_value(self.data['status'][status_type]))
            )

        failed_conds = []
        for cond in [x for x in self.data['status']['conditions'] if x['type'].lower() == "available"]:
            if cond['status'] != 'True':
                failed_conds.append(cond['type'])

        data_to_send.append(ZabbixMetric(
            zabbix_host, 'check_kubernetesd[get,deployments,%s,%s,available_status]' % (self.name_space, self.name),
            failed_conds if len(failed_conds) > 0 else 'OK')
        )
        return data_to_send
