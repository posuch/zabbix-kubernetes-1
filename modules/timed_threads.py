import logging
import threading
import time


class TimedThread(threading.Thread):
    stop_thread = False
    restart_thread = False
    daemon = None

    # TODO: change default of delay_first_run_seconds to 120 seconds
    def __init__(self, resource, interval, exit_flag, daemon, daemon_method, delay_first_run=False, delay_first_run_seconds=10):
        self.cycle_interval_seconds = interval
        self.exit_flag = exit_flag
        self.resource = resource
        self.daemon = daemon
        self.daemon_method = daemon_method
        self.delay_first_run = delay_first_run
        self.delay_first_run_seconds = delay_first_run_seconds
        threading.Thread.__init__(self, target=self.run)
        self.logger = logging.getLogger(self.__class__.__name__)

    def stop(self):
        self.logger.info('OK: Thread "' + self.resource + '" is stopping"')
        self.stop_thread = True

    def run(self):
        if self.delay_first_run:
            self.logger.info('%s -> %s | delaying first run by %is' % (self.resource, self.daemon_method, self.delay_first_run_seconds))
            time.sleep(self.delay_first_run_seconds)

        self.logger.debug('first looprun on timed thread %s.%s [interval %is]' % (self.resource, self.daemon_method, self.cycle_interval_seconds))
        getattr(self.daemon, self.daemon_method)(self.resource)
        self.logger.debug('first looprun complete on timed thread %s.%s [interval %is]' % (self.resource, self.daemon_method, self.cycle_interval_seconds))
        while not self.exit_flag.wait(self.cycle_interval_seconds):
            self.logger.debug('looprun on timed thread %s.%s [interval %is]' % (self.resource, self.daemon_method, self.cycle_interval_seconds))
            getattr(self.daemon, self.daemon_method)(self.resource)
            self.logger.debug('looprun complete on timed thread %s.%s [interval %is]' % (self.resource, self.daemon_method, self.cycle_interval_seconds))
        self.logger.info('terminating looprun thread %s.%s' % (self.resource, self.daemon_method))
