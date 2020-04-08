import json
import logging

from pyzabbix import ZabbixMetric
from .k8sobject import K8sObject, transform_value

logger = logging.getLogger(__name__)


class Pod(K8sObject):
    object_type = 'pod'

    @property
    def base_name(self):
        for container in self.data['spec']['containers']:
            if container['name'] in self.name:
                return container['name']
        return self.name

    @property
    def resource_data(self):
        data = super().resource_data
        containers = {}
        for container in self.data['spec']['containers']:
            containers.setdefault(container['name'], 0)
            containers[container['name']] += 1

        data['containers'] = json.dumps(containers)
        container_status = dict()
        data['ready'] = True
        data['pod_data'] = {
            "restart_count": 0,
            "ready": 0,
            "not_ready": 0,
            "status": "OK",
        }

        if "container_statuses" in self.data['status'] and self.data['status']['container_statuses']:
            for container in self.data['status']['container_statuses']:
                status_values = []
                container_name = container['name']

                # this pod data
                if container_name not in container_status:
                    container_status[container_name] = {
                        "restart_count": 0,
                        "ready": 0,
                        "not_ready": 0,
                        "status": "OK",
                    }
                container_status[container_name]['restart_count'] += container['restart_count']
                data['pod_data']['restart_count'] += container['restart_count']

                if container['ready'] is True:
                    container_status[container_name]['ready'] += 1
                    data['pod_data']['ready'] += 1
                else:
                    container_status[container_name]['not_ready'] += 1
                    data['pod_data']['not_ready'] += 1

                if container["state"] and len(container["state"]) > 0:
                    for status, container_data in container["state"].items():
                        if container_data and status != "running":
                            status_values.append(status)

                if len(status_values) > 0:
                    container_status[container_name]['status'] = 'ERROR: ' + (','.join(status_values))
                    data['pod_data']['status'] = container_status[container_name]['status']
                    data['ready'] = False

        data['container_status'] = container_status
        data['status'] = json.dumps(container_status)
        return data

    # def get_zabbix_metrics(self):
    #     data = self.resource_data
    #     data_to_send = list()
    #
    #     if 'status' not in data:
    #         logger.error(data)
    #
    #     for k, v in data['pod_data'].items():
    #         data_to_send.append(ZabbixMetric(
    #             self.zabbix_host, 'check_kubernetesd[get,pods,%s,%s,%s]' % (self.name_space, self.name, k),
    #             v,
    #         ))
    #
    #     return data_to_send
