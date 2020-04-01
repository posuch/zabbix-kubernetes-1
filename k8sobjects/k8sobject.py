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


def transform_value(value):
    if value is None:
        return 0
    m = re.match(r"^(\d+)Ki$", str(value))
    if m:
        return int(m.group(1)) * 1024
    return value


def get_k8s_class_identifier(resource):
    return dict(
        nodes='node',
        components='component',
        services='service',
        deployments='deployment',
        statefulsets='statefulset',
        daemonsets='daemonset',
        pods='pod',
        tls='tls',
        ingresses='ingress',
    )[resource]


def slugit(name_space, name, maxlen):
    if name_space:
        slug = name_space + '/' + name
    else:
        slug = name

    if len(slug) <= maxlen:
        return slug

    prefix_pos = int((maxlen / 2) - 1)
    suffix_pos = len(slug) - int(maxlen / 2) - 2
    return slug[:prefix_pos] + "~" + slug[suffix_pos:]


class K8sResourceManager:
    def __init__(self, resource):
        self.resource = resource
        self.objects = dict()
        # containers only used for pods
        self.containers = dict()

        mod = importlib.import_module('k8sobjects')
        class_label = get_k8s_class_identifier(resource)
        self.resource_class = getattr(mod, class_label.capitalize(), None)

    def add_obj(self, obj):
        if not self.resource_class:
            logger.error('No Resource Class found for "%s"' % self.resource)
            return

        new_obj = self.resource_class(obj, self.resource, manager=self)
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
        if not self.resource_class:
            logger.error('No Resource Class found for "%s"' % self.resource)
            return

        resourced_obj = self.resource_class(obj, self.resource, manager=self)
        deleted_obj = None

        if resourced_obj.uid in self.objects:
            deleted_obj = json.loads(
                json.dumps(
                    self.objects[resourced_obj.uid],
                    sort_keys=True,
                    default=json_encoder,
                )
            )
            del self.objects[resourced_obj.uid]
        return deleted_obj


class K8sObject:
    def __init__(self, obj_data, resource, manager=None):
        self.is_dirty = True
        self.last_sent = 0
        self.resource = resource
        self.data = obj_data
        self.data_checksum = self.calculate_checksum()
        self.manager = manager

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
        return self.object_type + '_' + self.name

    @property
    def name(self):
        name = self.data.get('metadata', {}).get('name')
        if not name:
            logger.warning('Could not find name for obj %s' % self)
        return name

    @property
    def name_space(self):
        from .node import Node
        from .component import Component
        if isinstance(self, Node) or isinstance(self, Component):
            return None

        name_space = self.data.get('metadata', {}).get('namespace')
        if not name_space:
            logger.warning('Could not find name_space for obj %s' % self.name)
        return name_space

    def calculate_checksum(self):
        return hashlib.md5(
            json.dumps(
                self.data,
                sort_keys=True,
                default=json_encoder,
            ).encode('utf-8')
        ).hexdigest()

    def get_zabbix_discovery_metrics(self):
        return [{
            "{#NAME}": self.name,
            "{#NAMESPACE}": self.name_space,
            "{#SLUG}": slugit(self.name_space, self.name, 40),
        }]

    def get_discovery_for_zabbix(self, discovery_metrics):
        return json.dumps({
            'data': discovery_metrics,
        })
