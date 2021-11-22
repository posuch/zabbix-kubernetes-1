k8s_config_type = "incluster"
k8s_api_host = 'https://example.kube-apiserver.com'
k8s_api_token = ''
verify_ssl = True
debug = False
debug_k8s_events = False
resources_exclude = []
namespace_exclude_re = None

sentry_enabled = False
sentry_dsn = ""

zabbix_server = 'example.zabbix-server.com'
zabbix_resources_exclude = ["components", "statefulsets", "daemonsets"]
zabbix_host = 'k8s-example-host'
zabbix_debug = False
zabbix_single_debug = False
zabbix_dry_run = False

web_api_enable = False
web_api_resources_exclude = ["daemonsets", "components", "services", "statefulsets"]
web_api_verify_ssl = True
web_api_host = "https://example.api.com/api/v1/k8s"
web_api_token = ""
web_api_cluster = 'k8s-test-cluster'

discovery_interval_fast = 60 * 15
resend_data_interval_fast = 60 * 2

discovery_interval_slow = 60 * 60 * 2
resend_data_interval_slow = 60 * 30
