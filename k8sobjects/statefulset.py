import logging

from pyzabbix import ZabbixMetric
from .k8sobject import K8sObject, transform_value

logger = logging.getLogger(__name__)


class Statefulset(K8sObject):
    object_type = 'statefulset'

    @property
    def resource_data(self):
        data = super().resource_data
        return data
