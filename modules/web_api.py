import requests
import logging

from k8sobjects import get_k8s_class_identifier

logger = logging.getLogger(__name__)


class WebApi:
    def __init__(self, api_host, api_token, verify_ssl=True):
        self.api_host = api_host
        self.api_token = api_token
        self.verify_ssl = verify_ssl

    def get_headers(self):
        return {
            'Authorization': self.api_token,
        }

    def get_url(self, resource):
        api_resource = get_k8s_class_identifier(resource)

        url = self.api_host
        if not url.endswith('/'):
            url += '/'
        return url + api_resource + '/'

    def send_data(self, resource, data, action):
        url = self.get_url(resource)

        if action.lower() == 'added':
            func = requests.post
        elif action.lower() == 'modified':
            func = requests.put
        else:
            return

        r = func(url,
                 data=data,
                 headers=self.get_headers(),
                 verify=self.verify_ssl)

        if r.status_code > 399:
            logger.warning('[%s] %s sended %s data -> %s' % (r.status_code, url, resource, data))
            logger.warning(r.text)
        else:
            logger.debug('[%s] sended %s [%s]' % (r.status_code, resource, data['name']))
