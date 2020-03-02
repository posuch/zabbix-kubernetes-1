import logging
import threading


class TimedThread(threading.Thread):
    stop_thread = False
    restart_thread = False
    daemon = None

    def __init__(self, resource, interval, exit_flag, daemon, daemon_method, delay=False):
        self.cycle_interval_seconds = interval
        self.exit_flag = exit_flag
        self.resource = resource
        self.daemon = daemon
        self.daemon_method = daemon_method
        self.delay = delay
        threading.Thread.__init__(self, target=self.run)
        self.logger = logging.getLogger(self.__class__.__name__)

    def stop(self):
        self.logger.info('OK: Thread "' + self.resource + '" is stopping"')
        self.stop_thread = True

    def run(self):
        self.logger.debug('starting thread %s | interval: %s' % (self.resource, self.cycle_interval_seconds))
        if not self.delay:
            getattr(self.daemon, self.daemon_method)(self.resource)

        while not self.exit_flag.wait(self.cycle_interval_seconds):
            self.logger.info('starting new run on thread ' + self.resource + '.' + self.daemon_method)
            getattr(self.daemon, self.daemon_method)(self.resource)
