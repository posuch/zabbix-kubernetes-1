import logging

from pyzabbix import ZabbixMetric
from .k8sobject import K8sObject, transform_value

logger = logging.getLogger(__name__)


class Daemonset(K8sObject):
    object_type = 'daemonset'

    @property
    def resource_data(self):
        data = super().resource_data
        return data

    def get_zabbix_metrics(self, zabbix_host):
        data = self.resource_data
        data_to_send = list()
        return data_to_send
