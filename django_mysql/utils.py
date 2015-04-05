# -*- coding:utf-8 -*-
from __future__ import division

import os
import pty
import time
from contextlib import contextmanager
from subprocess import PIPE, Popen, call
from threading import Lock, Thread

from django.utils.six.moves.queue import Empty, Queue


class WeightedAverageRate(object):
    """
    Adapted from percona-toolkit - provides a weighted average counter to keep
    at a certain rate of activity (row iterations etc.).
    """
    def __init__(self, target_t, weight=0.75):
        """
        target_t - Target time for t in update()
        weight - Weight of previous n/t values
        """
        self.target_t = target_t
        self.avg_n = 0.0
        self.avg_t = 0.0
        self.weight = weight

    def update(self, n, t):
        """
        Update weighted average rate.  Param n is generic; it's how many of
        whatever the caller is doing (rows, checksums, etc.).  Param s is how
        long this n took, in seconds (hi-res or not).

        Parameters:
            n - Number of operations (rows, etc.)
            t - Amount of time in seconds that n took

        Returns:
            n adjusted to meet target_t based on weighted decaying avg rate
        """
        if self.avg_n and self.avg_t:
            self.avg_n = (self.avg_n * self.weight) + n
            self.avg_t = (self.avg_t * self.weight) + t
        else:
            self.avg_n = n
            self.avg_t = t

        avg_rate = self.avg_n / self.avg_t
        new_n = int(avg_rate * self.target_t)
        return new_n


class StopWatch(object):
    """
    Context manager for timing a block
    """
    def __enter__(self):
        self.start_time = time.time()
        return self

    def __exit__(self, *args, **kwargs):
        self.end_time = time.time()
        self.total_time = self.end_time - self.start_time


@contextmanager
def noop_context(*args, **kwargs):
    yield


def settings_to_cmd_args(settings_dict):
    """
    Copied from django 1.8 MySQL backend DatabaseClient - where the runshell
    commandline creation has been extracted and made callable like so.
    """
    args = ['mysql']
    db = settings_dict['OPTIONS'].get('db', settings_dict['NAME'])
    user = settings_dict['OPTIONS'].get('user', settings_dict['USER'])
    passwd = settings_dict['OPTIONS'].get('passwd', settings_dict['PASSWORD'])
    host = settings_dict['OPTIONS'].get('host', settings_dict['HOST'])
    port = settings_dict['OPTIONS'].get('port', settings_dict['PORT'])
    cert = settings_dict['OPTIONS'].get('ssl', {}).get('ca')
    defaults_file = settings_dict['OPTIONS'].get('read_default_file')
    # Seems to be no good way to set sql_mode with CLI.

    if defaults_file:
        args += ["--defaults-file=%s" % defaults_file]
    if user:
        args += ["--user=%s" % user]
    if passwd:
        args += ["--password=%s" % passwd]
    if host:
        if '/' in host:
            args += ["--socket=%s" % host]
        else:
            args += ["--host=%s" % host]
    if port:
        args += ["--port=%s" % port]
    if cert:
        args += ["--ssl-ca=%s" % cert]
    if db:
        args += [db]
    return args


programs_memo = {}


def have_program(program_name):
    global programs_memo
    if program_name not in programs_memo:
        status = call(['which', program_name], stdout=PIPE)
        programs_memo[program_name] = (status == 0)

    return programs_memo[program_name]


def pt_fingerprint(query):
    """
    Takes a query (in a string) and returns its 'fingerprint'
    """
    if not have_program('pt-fingerprint'):  # pragma: no cover
        raise OSError("pt-fingerprint doesn't appear to be installed")

    thread = PTFingerprintThread.get_thread()
    thread.in_queue.put(query)
    return thread.out_queue.get()


class PTFingerprintThread(Thread):
    """
    Class for a singleton background thread to pass queries to pt-fingerprint
    and get their fingerprints back. This is done because the process launch
    time is relatively expensive and it's useful to be able to fingerprinting
    queries quickly.

    The get_thread() class method returns the singleton thread - either
    instantiating it or returning the existing one.

    The thread launches pt-fingerprint with subprocess and then takes queries
    from an input queue, passes them the subprocess and returns the fingerprint
    to an output queue. If it receives no queries in PROCESS_LIFETIME seconds,
    it closes the subprocess and itself - so you don't have processes hanging
    around.
    """

    the_thread = None
    life_lock = Lock()

    PROCESS_LIFETIME = 60.0  # seconds

    @classmethod
    def get_thread(cls):
        with cls.life_lock:
            if cls.the_thread is None:
                in_queue = Queue()
                out_queue = Queue()
                thread = cls(in_queue, out_queue)
                thread.daemon = True
                thread.in_queue = in_queue
                thread.out_queue = out_queue
                thread.start()
                cls.the_thread = thread

        return cls.the_thread

    def __init__(self, in_queue, out_queue, **kwargs):
        self.in_queue = in_queue
        self.out_queue = out_queue
        super(PTFingerprintThread, self).__init__(**kwargs)

    def run(self):
        global fingerprint_thread
        master, slave = pty.openpty()
        proc = Popen(
            ['pt-fingerprint'],
            stdin=PIPE,
            stdout=slave,
            close_fds=True
        )
        stdin = proc.stdin
        stdout = os.fdopen(master)

        while True:
            try:
                query = self.in_queue.get(timeout=self.PROCESS_LIFETIME)
            except Empty:
                self.life_lock.acquire()
                # We timed out, but there was something put into the queue
                # since
                if (
                    self.__class__.the_thread is self and
                    self.in_queue.qsize()
                ):  # pragma: no cover
                    self.life_lock.release()
                    break
                # Die
                break

            stdin.write(query.encode('utf-8'))
            if not query.endswith(';'):
                stdin.write(';'.encode('ascii'))
            stdin.write('\n'.encode('ascii'))
            stdin.flush()
            fingerprint = stdout.readline()
            self.out_queue.put(fingerprint.strip())

        stdin.close()
        self.__class__.the_thread = None
        self.life_lock.release()
