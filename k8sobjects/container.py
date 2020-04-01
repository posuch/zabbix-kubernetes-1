from pyzabbix import ZabbixMetric

import logging

from .k8sobject import K8sObject, transform_value

logger = logging.getLogger(__name__)


class Container(K8sObject):
    object_type = 'container'

    def get_zabbix_metrics(self, zabbix_host):
        data = self.resource_data
        data_to_send = list()

        if 'status' not in data:
            logger.error(data)

        for container_name, data in self.manager.containers.items():
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

