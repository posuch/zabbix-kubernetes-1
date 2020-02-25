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
            self.objects[new_obj.uid] = new_obj

        # return created or updated object
        return self.objects[new_obj.uid]


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


class Pod(K8sObject):
    object_type = 'pod'


class Deployment(K8sObject):
    object_type = 'deployment'
