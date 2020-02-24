import datetime
import importlib
import hashlib
import json
import logging

logger = logging.getLogger(__name__)


def json_encoder(obj):
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()


class K8sResourceManager:
    resource_class = None
    objects = dict()

    def __init__(self, resource):
        mod = importlib.import_module('k8sobjects')
        class_label = resource
        if class_label.endswith('s'):
            class_label = class_label[:-1]

        self.resource_class = getattr(mod, class_label.capitalize(), None)

    def add_obj(self, obj):
        new_obj = self.resource_class(obj)
        if new_obj.uid not in self.objects:
            # new object
            self.objects[new_obj.uid] = new_obj
        elif self.objects[new_obj.uid].data_checksum != new_obj.data_checksum:
            # existing object with modified data
            self.objects[new_obj.uid] = new_obj

        # return created or updated object
        return self.objects[new_obj.uid]


class K8sObject:
    name_space = ''
    name = ''
    last_sent = 0
    is_dirty = True
    data = dict()
    data_checksum = ''

    def __init__(self, obj_data):
        self.data = obj_data
        self.data_checksum = self.calculate_checksum()

    def __str__(self):
        return self.uid

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
