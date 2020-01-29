import requests


class WebApi:
    def __init__(self, api_host, api_token, verify_ssl=True):
        self.api_host = api_host
        self.api_token = api_token
        self.verify_ssl = verify_ssl

    def get_headers(self):
        return {
            'Authorization': self.api_token,
        }

    def get_url(self, resource, obj):
        url = self.api_host
        if not url.endswith('/'):
            url += '/'
        return self.api_host + resource + '/'

    def prepare_data(self, resource, obj, token):
        return dict()

    def send_data(self, resource, obj, action):
        url = self.get_url(resource, obj)
        data = self.prepare_data(resource, obj, action)

        if action.lower() == 'added':
            requests.post(url,
                          data=data,
                          headers=self.get_headers(),
                          verify=self.verify_ssl)
        elif action.lower() == 'modified':
            requests.put(url,
                         data=data,
                         headers=self.get_headers(),
                         verify=self.verify_ssl)
