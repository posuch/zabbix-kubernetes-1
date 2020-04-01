import json
import logging

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

        data['containers'] = json.dumps(containers)
        container_status = dict()

        if "container_statuses" in self.data['status'] and self.data['status']['container_statuses']:
            for container in self.data['status']['container_statuses']:
                status_values = []
                container_name = container['name']

                # shared containers data
                if self.name_space not in self.manager.containers:
                    self.manager.containers[self.name_space] = dict()

                if container_name not in self.manager.containers[self.name_space]:
                    self.manager.containers[self.name_space][container_name] = {
                        "restart_count": 0,
                        "ready": 0,
                        "not_ready": 0,
                        "status": "OK",
                    }

                # this pod data
                if container_name not in container_status:
                    container_status[container_name] = {
                        "restart_count": 0,
                        "ready": 0,
                        "not_ready": 0,
                        "status": "OK",
                    }

                if container['ready'] is True:
                    self.manager.containers[self.name_space][container_name]["ready"] += 1
                    container_status[container_name]['ready'] += 1
                else:
                    self.manager.containers[self.name_space][container_name]["not_ready"] += 1
                    container_status[container_name]['not_ready'] += 1

                if container["state"] and len(container["state"]) > 0:
                    for status, container_data in container["state"].items():
                        if container_data and status != "running":
                            status_values.append(status)

                if len(status_values) > 0:
                    self.manager.containers[self.name_space][container_name]["status"] = 'ERROR: ' + (','.join(status_values))
                    container_status[container_name]['status'] = 'ERROR: ' + (','.join(status_values))

        data['status'] = json.dumps(container_status)
        return data

    def get_zabbix_metrics(self, zabbix_host):
        data = self.resource_data
        data_to_send = list()
        return data_to_send
