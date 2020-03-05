import re
import datetime
import importlib
import hashlib
import json
import logging

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
        name_space = self.data.get('metadata', {}).get('name')
        name = self.data.get('metadata', {}).get('name')
        if not hasattr(self, 'object_type'):
            raise AttributeError('No object_type set! Dont use K8sObject itself!')
        elif not name_space or not name:
            raise AttributeError('No name_space or name set for K8sObject.uid! name_space: %s, name: %s' % (name_space, name))

        return self.object_type + '_' + name_space + '_' + name

    @property
    def name(self):
        return self.data.get('metadata', {}).get('name')

    @property
    def name_space(self):
        return self.data.get('metadata', {}).get('namespace')

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

    @property
    def resource_data(self):
        data = super().resource_data
        return data


class Pod(K8sObject):
    object_type = 'pod'

    @property
    def resource_data(self):
        data = super().resource_data
        return data


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
        data.update({'failed cons': failed_conds})
        return data
