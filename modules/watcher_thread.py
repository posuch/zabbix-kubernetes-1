import logging
import threading

from urllib3.exceptions import ProtocolError


class WatcherThread(threading.Thread):
    stop_thread = False
    restart_thread = False
    daemon = None

    def __init__(self, resource, exit_flag, daemon, daemon_method, discovery=False):
        self.exit_flag = exit_flag
        self.resource = resource
        self.daemon = daemon
        self.daemon_method = daemon_method
        self.discovery = discovery
        threading.Thread.__init__(self, target=self.run)
        self.logger = logging.getLogger(self.__class__.__name__)

    def stop(self):
        self.logger.info('OK: Thread "' + self.resource + '" is stopping"')
        self.stop_thread = True

    def run(self):
        self.logger.info('starting looping watcher thread %s [discovery: %s]' % (self.resource, self.discovery))
        try:
            getattr(self.daemon, self.daemon_method)(self.resource, discovery=self.discovery)
        except ProtocolError as e:
            self.logger.error(e)
            self.daemon.dirty_threads = True
            self.restart_thread = True
