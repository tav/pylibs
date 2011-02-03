# Public Domain (-) 2011 The Ampify Authors.
# See the Ampify UNLICENSE file for details.

import readline
import sys

from cPickle import dumps, loads
from os import pipe
from socket import AF_UNIX, SOCK_STREAM, socket
from struct import calcsize, pack, unpack
from time import time
from uuid import uuid4

from Crypto.Random import atfork

from fabric.context_managers import settings
from fabric.operations import execute, local, reboot, run, sudo
from fabric.state import env
from fabric.utils import abort

try:
    from os import close, fork, read, remove, write
    from signal import SIGALRM, alarm, signal
except ImportError:
    forkable = False
else:
    forkable = True

# ------------------------------------------------------------------------------
# Helper I/O Interface
# ------------------------------------------------------------------------------

class DevNull:
    """Provide a file-like interface emulating /dev/null."""

    def __call__(self, *args, **kwargs):
        pass

    def flush(self):
        pass

    def log(self, *args, **kwargs):
        pass

    def write(self, input):
        pass

# ------------------------------------------------------------------------------
# Some Constants
# ------------------------------------------------------------------------------

dev_null = DevNull()
index_header_size = calcsize('H')

# ------------------------------------------------------------------------------
# Core Context Class
# ------------------------------------------------------------------------------

class ContextRunner(object):
    """A convenience class to support operations on initialised contexts."""

    def __init__(self, *args):
        if not args and env.ctx:
            self.ctx = env.ctx
            return
        if len(args) == 1 and not isinstance(args[0], basestring):
            args = tuple(args[0])
        self.ctx = args

    def execute(
        self, script, name=None, verbose=True, shell=True, pty=True,
        combine_stderr=True, dir=None
        ):
        responses = []; out = responses.append; e = execute
        for kwargs in env.get_settings_for_context(self.ctx):
            with settings(ctx=self.ctx, **kwargs):
                out(e(script, name, verbose, shell, pty, combine_stderr, dir))
        return responses

    def local(self, command, capture=True, dir=None, raw=False):
        responses = []; out = responses.append; l = local
        for kwargs in env.get_settings_for_context(self.ctx):
            with settings(ctx=self.ctx, **kwargs):
                out(l(command, capture, dir, raw))
        return responses

    def reboot(self, wait):
        for kwargs in env.get_settings_for_context(self.ctx):
            with settings(ctx=self.ctx, **kwargs):
                reboot(wait)

    def run(
        self, command, shell=True, pty=True, combine_stderr=True, dir=None,
        raw=False
        ):
        responses = []; out = responses.append
        if isinstance(command, basestring):
            r = run
            for kwargs in env.get_settings_for_context(self.ctx):
                with settings(ctx=self.ctx, **kwargs):
                    out(r(command, shell, pty, combine_stderr, dir, raw))
        else:
            for kwargs in env.get_settings_for_context(self.ctx):
                with settings(ctx=self.ctx, **kwargs):
                    out(command())
        return responses

    def shell(self, shell=True, pty=True, combine_stderr=True, dir=None):
        kwargs_list = env.get_settings_for_context(self.ctx)
        if not kwargs_list:
            return
        print
        print ">> !! entering shell mode !!"
        l, r = local, run
        count = 0
        try:
            while 1:
                try:
                    command = raw_input('>> ').strip()
                except EOFError:
                    raise KeyboardInterrupt
                if not command:
                    continue
                local_cmd = raw = 0
                if command.startswith('local '):
                    local_cmd = 1
                    command = command[6:].strip()
                    if not command:
                        continue
                if command.startswith('!'):
                    raw = 1
                    command = command[1:].strip()
                    if not command:
                        continue
                if local_cmd:
                    for kwargs in kwargs_list:
                        with settings(ctx=self.ctx, **kwargs):
                            try:
                                l(command, capture=0, dir=dir, raw=raw)
                            except KeyboardInterrupt:
                                print
                                count += 1
                                if count > 2:
                                    raise KeyboardInterrupt
                        count = 0
                else:
                    for kwargs in kwargs_list:
                        with settings(ctx=self.ctx, **kwargs):
                            try:
                                r(command, shell, pty, combine_stderr, dir, raw)
                            except KeyboardInterrupt:
                                print
                                count += 1
                                if count > 2:
                                    raise KeyboardInterrupt
                        count = 0
        except KeyboardInterrupt:
            print
            print ">> !! exiting shell mode !!"

    def sudo(
        self, command, shell=True, pty=True, combine_stderr=True, user=None,
        dir=None, raw=False
        ):
        responses = []; out = responses.append; s = sudo
        for kwargs in env.get_settings_for_context(self.ctx):
            with settings(ctx=self.ctx, **kwargs):
                out(s(command, shell, pty, combine_stderr, user, dir, raw))
        return responses

    if forkable:

        def multirun(
            self, command, shell=True, pty=True, combine_stderr=True, dir=None,
            raw=False, warn_only=True, condensed=False
            ):
            settings_list = env.get_settings_for_context(self.ctx)
            if not settings_list:
                return []
            env.disable_char_buffering = 1
            try:
                return self._multirun(
                    command, settings_list, shell, pty, combine_stderr, dir,
                    raw, warn_only, condensed
                    )
            finally:
                env.disable_char_buffering = 0

        def _multirun(
            self, command, settings_list, shell, pty, combine_stderr, dir, raw,
            warn_only, condensed
            ):

            for setting in settings_list:
                if 'warn_only' not in setting:
                    setting['warn_only'] = warn_only
                setting['ctx'] = self.ctx

            callable_command = hasattr(command, '__call__')
            done = 0
            idx = 0
            processes = {}
            total = len(settings_list)
            pool_size = env.multirun_pool_size
            socket_path = '/tmp/fab.%s' % uuid4()

            server = socket(AF_UNIX, SOCK_STREAM)
            server.bind(socket_path)
            server.listen(pool_size)

            for client_id in range(min(pool_size, total)):
                from_parent, to_child = pipe()
                pid = fork()
                if pid:
                    processes[client_id] = [from_parent, to_child, pid, idx]
                    idx += 1
                    write(to_child, pack('H', idx))
                else:
                    atfork()
                    signal(SIGALRM, lambda *args: sys.exit())
                    if condensed:
                        sys.__ori_stdout__ = sys.stdout
                        sys.__ori_stderr__ = sys.stderr
                        sys.stdout = sys.stderr = dev_null
                    while 1:
                        alarm(env.multirun_child_timeout)
                        data = read(from_parent, index_header_size)
                        alarm(0)
                        idx = unpack('H', data)[0] - 1
                        if idx == -1:
                            sys.exit()
                        client = socket(AF_UNIX, SOCK_STREAM)
                        client.connect(socket_path)
                        try:
                            if callable_command:
                                with settings(**settings_list[idx]):
                                    response = command()
                            else:
                                with settings(**settings_list[idx]):
                                    response = run(
                                        command, shell, pty, combine_stderr,
                                        dir, raw
                                        )
                        except Exception, error:
                            client.send(dumps((client_id, idx, error)))
                            client.close()
                        else:
                            client.send(dumps((client_id, idx, response)))
                            client.close()

            responses = [None] * total
            if condensed:
                stdout = sys.stdout
                if callable_command:
                    command = '%s()' % command.__name__
                else:
                    command = command
                if total < pool_size:
                    print (
                        "[system] Running %r on %s hosts" % (command, total)
                        )
                else:
                    print (
                        "[system] Running %r on %s hosts with pool of %s" %
                        (command, total, pool_size)
                        )
                template = "[system] %%s/%s completed ..." % total
                info = template % 0
                written = len(info) + 1
                stdout.write(info)
                stdout.flush()

            while done < total:
                conn, addr = server.accept()
                stream = []; buffer = stream.append
                while 1:
                    data = conn.recv(1024)
                    if not data:
                        break
                    buffer(data)
                client_id, resp_idx, response = loads(''.join(stream))
                responses[resp_idx] = response
                done += 1
                spec = processes[client_id]
                if idx < total:
                    spec[3] = idx
                    idx += 1
                    write(spec[1], pack('H', idx))
                else:
                    spec = processes.pop(client_id)
                    write(spec[1], pack('H', 0))
                    close(spec[0])
                    close(spec[1])
                if condensed:
                    stdout.write('\x08' * written)
                    print (
                        "[system] Finished on %s" %
                        settings_list[resp_idx]['host_string']
                        )
                    if done == total:
                        info = "[system] %s/%s completed successfully!" % (
                            done, done
                            )
                    else:
                        info = template % done
                    written = len(info) + 1
                    stdout.write(info)
                    stdout.flush()

            if condensed:
                stdout.write('\n')

            server.close()
            remove(socket_path)
            return responses

    else:

        def multirun(self, *args, **kwargs):
            abort("multirun is not supported on this setup")

    @property
    def settings(self):
        return env.get_settings_for_context(self.ctx)

# ------------------------------------------------------------------------------
# Utility API Functions
# ------------------------------------------------------------------------------

def failed(responses):
    return any(isinstance(resp, Exception) or resp.failed for resp in responses)

def succeeded(responses):
    return all(
        (not isinstance(resp, Exception)) and resp.succeeded
        for resp in responses
        )




# load_settings
# vars
# colors = True
# builtins
