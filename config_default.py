k8s_api_host = 'https://example.kube-apiserver.com'
k8s_api_token = ''
verify_ssl = True
debug = False
debug_k8s_events = False
zabbix_server = 'example.zabbix-server.com'
zabbix_host = 'k8s-example-host'
zabbix_debug = False
zabbix_single_debug = False
zabbix_dry_run = False

web_api_enable = False
web_api_verify_ssl = True
web_api_host = "https://example.api.com/api/v1/k8s"
web_api_token = ""
web_api_cluster = 'k8s-test-cluster'

discovery_interval_fast = 600
data_interval_fast = 60 * 2

discovery_interval_slow = 3600
data_interval_slow = 60 * 15

