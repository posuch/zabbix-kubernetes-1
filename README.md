k8s-zabbix
=================

This project provides kubernetes monitoring capabilities for zabbix.

* apiserver : Check and discover apiservers
* components : Check and discover health of k8s components (etcd, controller-manager, scheduler etc.)
* nodes: Check and discover active nodes
* pods: Check pods for restarts
* deployments: Check and discover deployments
* daemonsets: Check and discover daemonsets readiness
* replicasets: Check and discover replicasets readiness
* tls: Check tls secrets expiration dates

For details of the monitored kubernetes attributes, have a look at the [documentation](http://htmlpreview.github.io/?https://github.com/zabbix-tooling/k8s-zabbix/blob/master/template/documentation/custom_service_kubernetes.html)

The current docker image is published on https://hub.docker.com/repository/docker/scoopex666/k8s-zabbix/

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
  cp config_default.py configd_c1.py
  ./check_kubernetesd configd_c1
  ```
* Test in docker (IS ESSENTIAL FOR PUBLISH)
  ```
  ./build.sh default
  ```
* Create release
  ```
  git tag NEW_TAG
  git push --tags
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
  ./build.sh default
  MY_PRIVATE_REGISTRY="docker-registry.foo.bar"
  docker tag k8s-zabbix:latest $MY_PRIVATE_REGISTRY:k8s-zabbix:latest
  docker push $MY_PRIVATE_REGISTRY:k8s-zabbix:latest
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
  vi kubernetes/deployment.yaml # modify docker repo
  kubectl apply -f kubernetes/deployment.yaml
  ```

TODOs
=====

- use k8s watch api 
- gather and send data in dedicated threads with different intervals
- Check if it is useful to convert the deployment to a daemon set which runs on one and only one controller
  https://kubernetes.io/docs/concepts/workloads/controllers/daemonset/

Authors
=======

- Marc Schoechlin <marc.schoechlin@vico-research.com>
- Amin Dandache <amin.dandache@vico-research.com>

Licence
=======

see "[LICENSE](./LICENSE)" file
