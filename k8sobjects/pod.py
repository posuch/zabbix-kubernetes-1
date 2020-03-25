import logging

from pyzabbix import ZabbixMetric
from .k8sobject import K8sObject, transform_value

logger = logging.getLogger(__name__)


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
        status_values = []

        if "container_statuses" in self.data['status'] and self.data['status']['container_statuses']:
            for container in self.data['status']['container_statuses']:
                if self.name not in data['status']:
                    data['status'].setdefault(self.name,
                        {
                            "restart_count": 0,
                            "ready": 0,
                            "not_ready": 0,
                            "status": "OK",
                        }
                    )

                if container['ready'] is True:
                    data['status'][self.name]["ready"] += 1
                else:
                    data['status'][self.name]["not_ready"] += 1

                if container["state"] and len(container["state"]) > 0:
                    for status, container_data in container["state"].items():
                        if container_data and status != "running":
                            status_values.append(status)

        if len(status_values) > 0:
            data['status'][self.name]["status"] = 'ERROR: ' + (','.join(status_values))

        return data

    def get_zabbix_metrics(self, zabbix_host):
        data = self.resource_data
        data_to_send = list()

        if 'status' not in data:
            logger.error(data)

        for container_name, data in data['status'].items():
            data_to_send.append(ZabbixMetric(
                zabbix_host, 'check_kubernetesd[get,pods,%s,%s,ready]' % (self.name_space, self.name),
                data["ready"],
            ))
            data_to_send.append(ZabbixMetric(
                zabbix_host, 'check_kubernetesd[get,pods,%s,%s,not_ready]' % (self.name_space, self.name),
                data["not_ready"],
            ))
            data_to_send.append(ZabbixMetric(
                zabbix_host, 'check_kubernetesd[get,pods,%s,%s,restart_count]' % (self.name_space, self.name),
                data["restart_count"],
            ))
            data_to_send.append(ZabbixMetric(
                zabbix_host, 'check_kubernetesd[get,pods,%s,%s,status]' % (self.name_space, self.name),
                data["status"],
            ))

        return data_to_send

