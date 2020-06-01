import requests
import logging

from k8sobjects.k8sobject import K8S_RESOURCES

logger = logging.getLogger(__name__)


class WebApi:
    def __init__(self, api_host, api_token, verify_ssl=True):
        self.api_host = api_host
        self.api_token = api_token
        self.verify_ssl = verify_ssl

        url = self.get_url()
        r = requests.head(url)
        if r.status_code in [301, 302]:
            self.api_host = r.headers['location']

    def get_headers(self):
        return {
            'Authorization': self.api_token,
            'User-Agent': 'k8s-zabbix agent',
        }

    def get_url(self, resource=None):
        api_resource = None
        if resource:
            api_resource = K8S_RESOURCES[resource]

        url = self.api_host
        if not url.endswith('/'):
            url += '/'

        if not api_resource:
            return url
        return url + api_resource + '/'

    def send_data(self, resource, data, action):
        url = self.get_url(resource)

        if action.lower() == 'added':
            func = requests.post
        elif action.lower() == 'modified':
            func = requests.put
        else:
            return

        # empty variables are NOT sent!
        r = func(url,
                 data=data,
                 headers=self.get_headers(),
                 verify=self.verify_ssl,
                 allow_redirects=True)

        if r.status_code > 399:
            logger.warning('%s [%s] %s sended %s data -> %s (%s)' % (self.api_host, r.status_code, url, resource, data, action))
            logger.warning(r.text)
        else:
            logger.debug('%s [%s] sended %s [%s/%s] (%s)' % (url, r.status_code, resource, data['name_space'], data['name'], action))
