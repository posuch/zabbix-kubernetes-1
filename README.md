k8s-zabbix
=================

This project provides kubernetes monitoring capability for zabbix.

* apiserver : Check and discover apiservers
* components : Check and discover health of k8s components (etcd, controller-manager, scheduler etc.)
* nodes: Check and discover active nodes
* pods: Check pods for restarts
* deployments: Check and discover deployments
* daemonsets: Check and discover daemonsets readiness
* replicasets: Check and discover replicasets readiness
* tls: Check tls secrets expiration dates

For details of the monitored kubernetes attributes, have a look at the [documentation](http://htmlpreview.github.io/?https://github.com/zabbix-tooling/k8s-zabbix/blob/master/template/documentation/custom_service_kubernetes.html)

Testing and development
=======================


* Clone Repo and install dependencies
  ```
  git clone git@github.com:zabbix-tooling/k8s-zabbix.git
  virtualenv -p python3 venv
  source venv/bin/activate
  pip3 install -r requirements.txt
  ```
* Create monitoring account
  ```
  kubectl apply -f kubernetes/monitoring-user.yaml
  ```
* Gather API Key
  ```
  kubectl get secrets -n monitoring
  kubectl describe secret -n monitoring <id>
  ```
* Test
  ```
  source venv/bin/activate
  cp configd_example.py configd_c1.py
  ./check_kubernetesd configd_c1
  ```
* Test in docker
  ```
  ./build.sh default
  ```
* Creat release
  ```
  ./build.sh publish_image
  ```
Run in Kubernetes
=================

* Clone Repo and install dependencies
  ```
  git clone git@github.com:zabbix-tooling/k8s-zabbix.git
  ```
* Clone Repo and install dependencies
  ```
  docker build  -t k8s-zabbix:latest -f Dockerfile .
  docker inspect k8s-zabbix:latest --format='{{.Size}}MB'
  docker tag k8s-zabbix:latest docker-registry.foo.bar:k8s-zabbix:latest
  docker push docker-registry.foo.bar:k8s-zabbix:latest
  ```
* Get API Key
  ```
  kubectl get secrets -n monitoring
  kubectl describe secret -n monitoring <id>
  ```
* Create monitoring account and api service
  ```
  kubectl apply -f kubernetes/service-apiserver.yaml
  kubectl apply -f kubernetes/monitoring-user.yaml
  ```
* Create and apply deployment
  ```
  vi kubernetes/deployment.yaml
  kubectl apply -f kubernetes/deployment.yaml
  ```

TODOs
=====

- use k8s watch api 
- gather and send data in dedicated threads with different intervals

Authors
=======

- Marc Schoechlin <marc.schoechlin@vico-research.com>
- Amin Dandache <amin.dandache@vico-research.com>

Licence
=======

see "[LICENSE](./LICENSE)" file
