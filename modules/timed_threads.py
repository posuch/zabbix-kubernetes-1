import logging
import threading

class TimedThread(threading.Thread):
    stop_thread = False
    daemon = None

    def __init__(self, thread_name, interval, exit_flag, daemon, daemon_method):
        self.cycle_interval_seconds = interval
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
        self.logger.info('starting thread %s | interval: %s' % (self.thread_name, self.cycle_interval_seconds))
        getattr(self.daemon, self.daemon_method)()
        while not self.exit_flag.wait(self.cycle_interval_seconds):
            self.logger.info('starting new run on thread ' + self.thread_name + '.' + self.daemon_method)
            getattr(self.daemon, self.daemon_method)()