import os

from fcntl import LOCK_EX, LOCK_NB, flock
from time import sleep

LOCK_FILE = '/tmp/zabbix_kubernetes_lock_' + str(os.getuid())
SLEEP_TIME = 1
MODE_BLOCK = 'b'
MODE_NO_BLOCK = 'n'
MODE_RETRY = 'r'

VERBOSE = False


class UnableToLock(Exception):
    pass


class InvalidMode(Exception):
    pass


def file_lock(lock_file=LOCK_FILE, mode=MODE_RETRY, retries=60, sleep_time=SLEEP_TIME):
    """
    :param lock_file: full path to file that will be used as lock.
    :type lock_file: string
    :param mode: MODE_BLOCK, MODE_NO_BLOCK, MODE_RETRY)
    :type mode: string.
    :param retries: retry x times
    :type retries: int
    :param sleep_time: wait between retry
    :type sleep_time: int
    """

    def decorator(target):
        def wrapper(*args, **kwargs):
            try:
                if not (os.path.exists(lock_file) and os.path.isfile(lock_file)):
                    open(lock_file, 'a').close()
            except IOError as e:
                msg = 'Unable to create lock file: %s' % str(e)
                raise UnableToLock(msg)

            operation = LOCK_EX
            if mode in [MODE_NO_BLOCK, MODE_RETRY]:
                operation = LOCK_EX | LOCK_NB

            f = open(lock_file, 'a')
            if mode in [MODE_BLOCK, MODE_NO_BLOCK]:
                try:
                    flock(f, operation)
                except IOError as e:
                    msg = 'Unable to get exclusive lock: %s' % str(e)
                    raise UnableToLock(msg)

            elif mode == MODE_RETRY:
                for i in range(0, retries + 1):
                    try:
                        flock(f, operation)
                        break
                    except IOError as e:
                        if i == retries:
                            msg = 'Unable to get exclusive lock: %s' % str(e)
                            raise UnableToLock(msg)
                        sleep(sleep_time)

            else:
                raise InvalidMode('%s is not a valid mode.' % mode)

            try:
                result = target(*args, **kwargs)
            except Exception as e:
                f.close()
                raise e

            f.close()
            return result

        return wrapper

    return decorator
