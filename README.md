zabbix-kubernetes
=================

This project enhances zabbix with a externalscript which provides the following functionality:

* apiserver : Check and discover apiservers
* components : Check and discover health of k8s components (etcd, controller-manager, scheduler etc.)
* nodes: Check and discover active nodes
* deployments: Check and discover deployments
* daemonsets: Check and discover daemonsets readiness
* replicasets: Check and discover replicasets readiness
* tls: Check tls secrets expiration dates


Installation
=============


* Clone Repo and install dependencies
  ```
  cd /opt
  umask 0022
  git clone git@github.com:vico-research-and-consulting/zabbix-kubernetes.git
  pip3 install -r /opt/zabbix-kubernetes/requirements.txt
  ```
* Symlink externalscript to the externalscripts folder
  ```
  cd /etc/zabbix/externalscripts
  ln -s /opt/zabbix-kubernetes/check_kubernetes .
  ```
* Create monitoring account
  ```
  kubectl apply -f monitoring-user.yaml
  ```
* Create a configuration
  ```
  CLUSTERNAME="c1t"
  kubectl get secrets -n monitoring
  kubectl describe secret -n monitoring <id>
  cp /opt/zabbix-kubernetes/config_example.py /opt/zabbix-kubernetes/config_${CLUSTERNAME}.py
  ```
* Test 
  ```
  /opt/zabbix-kubernetes/check_kubernetes config_${CLUSTERNAME} discover deployments none none none|python -m json.tool
  /opt/zabbix-kubernetes/check_kubernetes config_${CLUSTERNAME} get deployments kube-system coredns replicas|python -m json.tool
  ```

  nodes deployments components tls pods


TODOs
=====


- Node Status Discoveren, neue Discovery node_conditions
  ```
            "status": {
 
                "conditions": [
                    {
                        "last_heartbeat_time": "2019-10-30T12:23:58+00:00",
                        "last_transition_time": "2019-10-30T12:23:58+00:00",
                        "message": "Calico is running on this node",
                        "reason": "CalicoIsUp",
                        "status": "False",
                        "type": "NetworkUnavailable"
                    },
                    {
                        "last_heartbeat_time": "2019-10-31T08:23:30+00:00",
                        "last_transition_time": "2019-10-30T10:44:51+00:00",
                        "message": "kubelet has sufficient memory available",
                        "reason": "KubeletHasSufficientMemory",
                        "status": "False",
                        "type": "MemoryPressure"
                    },

  ```

Authors
=======

- Marc Schoechlin <marc.schoechlin@vico-research.com>
- Amin Dandache <amin.dandache@vico-research.com>

Licence
=======

see "[LICENSE](./LICENSE)" file
