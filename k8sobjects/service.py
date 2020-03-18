import logging

from pyzabbix import ZabbixMetric
from .k8sobject import K8sObject, transform_value

logger = logging.getLogger(__name__)


class Service(K8sObject):
    object_type = 'service'

    @property
    def resource_data(self):
        data = super().resource_data
        if self.data["status"]["load_balancer"]["ingress"] is not None:
            data['is_ingress'] = True
        return data

    def get_zabbix_metrics(self, zabbix_host):
        data = self.resource_data
        data_to_send = list()
        return data_to_send
