import logging
import threading


class WatcherThread(threading.Thread):
    stop_thread = False
    daemon = None

    def __init__(self, thread_name, exit_flag, daemon, daemon_method):
        self.exit_flag = exit_flag
        self.thread_name = thread_name
        self.daemon = daemon
        self.daemon_method = daemon_method
        threading.Thread.__init__(self, target=self.run)
        self.logger = logging.getLogger(self.__class__.__name__)

    def stop(self):
        self.logger.info('OK: Thread "' + self.thread_name + '" is stopping"')
        self.stop_thread = True

    def run(self):
        self.logger.info('starting looping watcher thread %s' % self.thread_name)
        getattr(self.daemon, self.daemon_method)(self.thread_name)
