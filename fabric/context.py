# Public Domain (-) 2011 The Ampify Authors.
# See the Ampify UNLICENSE file for details.

import atexit
import sys

from cPickle import dumps, loads
from math import ceil
from os import X_OK, access, environ, getcwd, listdir, pathsep, pipe
from os.path import expanduser, isabs, isdir, join, normpath
from random import sample
from socket import AF_UNIX, SOCK_STREAM, error as socketerror, socket
from struct import calcsize, pack, unpack
from time import time
from traceback import format_exc
from uuid import uuid4

from Crypto.Random import atfork

from fabric.context_managers import settings
from fabric.operations import Blank, execute, local, reboot, run, sudo
from fabric.state import env, output
from fabric.utils import abort, fastprint, indent, warn

try:
    from errno import EAGAIN, EINTR, EPIPE
    from os import close, fork, kill, read, remove, write
    from signal import SIGALRM, SIGTERM, alarm, signal
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

builtins = {}
dev_null = DevNull()
index_header_size = calcsize('H')
shell_history_file = None

# ------------------------------------------------------------------------------
# Timeout
# ------------------------------------------------------------------------------

class ProcessTimeout(object):
    """Process timeout indicator."""

    failed = 1
    succeeded = 0

    def __bool__(self):
        return False

    __nonzero__ = __bool__

    def __str__(self):
        return 'TIMEOUT'

    __repr__ = __str__

TIMEOUT = ProcessTimeout()

class TimeoutException(Exception):
    """An internal timeout exception raised on SIGALRM."""

# ------------------------------------------------------------------------------
# Proxy Boolean
# ------------------------------------------------------------------------------

class WarningBoolean(object):
    """Proxy boolean to env.warning."""

    def __bool__(self):
        return env.warn_only

    __nonzero__ = __bool__


WarnOnly = WarningBoolean()

# ------------------------------------------------------------------------------
# Failure Handler
# ------------------------------------------------------------------------------

def handle_failure(cmd, warn_only):
    if hasattr(cmd, '__name__'):
        cmd = cmd.__name__ + '()'
    message = 'Error running `%s`\n\n%s' % (cmd, indent(format_exc()))
    if warn_only:
        warn(message)
    else:
        abort(message)

# ------------------------------------------------------------------------------
# Shell Spec
# ------------------------------------------------------------------------------

class ShellSpec(object):
    """Container class for shell spec variables."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

# ------------------------------------------------------------------------------
# Response List
# ------------------------------------------------------------------------------

class ResponseList(list):
    """Container class for response values."""

    @classmethod
    def new(klass, settings, value=None):
        if value:
            obj = klass(value)
        else:
            obj = klass()
        obj._settings = settings
        return obj

    def ziphost(self):
        for response, setting in zip(self, self._settings):
            yield response, setting['host_string']

    def zipsetting(self):
        for response, setting in zip(self, self._settings):
            yield response, setting

    @property
    def settings(self):
        return self._settings[:]

# ------------------------------------------------------------------------------
# Core Context Class
# ------------------------------------------------------------------------------

class ContextRunner(object):
    """A convenience class to support operations on initialised contexts."""

    def __init__(self, *args, **kwargs):
        if kwargs and 'settings' in kwargs:
            self.ctx = ('<sample>',)
            self._settings = kwargs['settings']
        if args:
            if len(args) == 1 and not isinstance(args[0], basestring):
                args = tuple(args[0])
            self.ctx = args
            self._settings = env.get_settings(args)
        else:
            if env.ctx:
                self.ctx = env.ctx
                self._settings = env.get_settings(env.ctx)
            else:
                self.ctx = ()
                self._settings = []

    def execute(
        self, script, name=None, verbose=True, shell=True, pty=True,
        combine_stderr=True, dir=None
        ):
        ctx, e = self.ctx, execute
        settings_list = self._settings
        responses = ResponseList.new(settings_list); out = responses.append
        for kwargs in settings_list:
            with settings(ctx=ctx, **kwargs):
                out(e(script, name, verbose, shell, pty, combine_stderr, dir))
        return responses

    def local(self, command, capture=True, dir=None, format=Blank):
        ctx, l = self.ctx, local
        settings_list = self._settings
        responses = ResponseList.new(settings_list); out = responses.append
        for kwargs in settings_list:
            with settings(ctx=ctx, **kwargs):
                out(l(command, capture, dir, format))
        return responses

    def reboot(self, wait):
        ctx, r = self.ctx, reboot
        settings_list = self._settings
        responses = ResponseList.new(settings_list); out = responses.append
        for kwargs in settings_list:
            with settings(ctx=ctx, **kwargs):
                out(r(wait))
        return responses

    def run(
        self, command, shell=True, pty=True, combine_stderr=True, dir=None,
        format=Blank, warn_only=WarnOnly
        ):
        ctx = self.ctx
        settings_list = self._settings
        responses = ResponseList.new(settings_list); out = responses.append
        if isinstance(command, basestring):
            r = run
            for kwargs in settings_list:
                with settings(ctx=ctx, warn_only=warn_only, **kwargs):
                    out(r(command, shell, pty, combine_stderr, dir, format))
        else:
            for kwargs in settings_list:
                with settings(ctx=ctx, warn_only=warn_only, **kwargs):
                    try:
                        out(command())
                    except Exception, error:
                        out(error)
                        handle_failure(command, warn_only)
        return responses

    def shell(
        self, builtins=builtins, shell=True, pty=True, combine_stderr=True,
        dir=None, format=Blank, warn_only=True
        ):
        ctx = self.ctx
        settings_list = self._settings
        if not settings_list:
            return
        global shell_history_file
        if (not shell_history_file) and readline:
            shell_history_file = expanduser(env.shell_history_file)
            try:
                readline.read_history_file(shell_history_file)
            except IOError:
                pass
            atexit.register(readline.write_history_file, shell_history_file)
        fastprint("shell mode\n\n", 'system')
        spec = ShellSpec(
            shell=shell, pty=pty, combine_stderr=combine_stderr, dir=dir,
            format=format
            )
        r = run
        count = 0
        prefix = '>> '
        if env.colors:
            prefix = env.color_settings['prefix'](prefix)
        try:
            while 1:
                try:
                    command = raw_input(prefix).strip()
                except EOFError:
                    raise KeyboardInterrupt
                if not command:
                    continue
                builtin_cmd = 0
                if command.startswith('.'):
                    if (len(command) > 1) and command[1].isalpha():
                        builtin_cmd = 1
                if builtin_cmd:
                    command = command.split(' ', 1)
                    if len(command) == 1:
                        command = command[0]
                        arg = ''
                    else:
                        command, arg = command
                    command = command[1:].strip()
                    if not command:
                        continue
                    command = command.replace('_', '-')
                    if command not in builtins:
                        warn("Couldn't find builtin command %r" % command)
                        continue
                    command = builtins[command]
                    if hasattr(command, '__single__'):
                        with settings(ctx=ctx, warn_only=warn_only):
                            try:
                                command(spec, arg)
                            except Exception:
                                handle_failure(command, warn_only)
                        continue
                for kwargs in settings_list:
                    with settings(ctx=ctx, warn_only=warn_only, **kwargs):
                        try:
                            if builtin_cmd:
                                try:
                                    command(spec, arg)
                                except Exception:
                                    handle_failure(command, warn_only)
                            else:
                                r(command, shell, pty, combine_stderr, dir,
                                  format)
                        except KeyboardInterrupt:
                            print
                            count += 1
                            if count > 2:
                                raise KeyboardInterrupt
                    count = 0
        except KeyboardInterrupt:
            print
            print
            fastprint("shell mode terminated\n", 'system')

    def sudo(
        self, command, shell=True, pty=True, combine_stderr=True, user=None,
        dir=None, format=Blank
        ):
        ctx, s = self.ctx, sudo
        settings_list = self._settings
        responses = ResponseList.new(settings_list); out = responses.append
        for kwargs in settings_list:
            with settings(ctx=ctx, **kwargs):
                out(s(command, shell, pty, combine_stderr, user, dir, format))
        return responses

    if forkable:

        def multilocal(
            self, command, capture=True, dir=None, format=Blank, warn_only=True,
            condensed=False, quiet_exit=True, laggards_timeout=None,
            wait_for=None
            ):
            def run_local():
                return local(command, capture, dir, format)
            return self.multirun(
                run_local, warn_only=warn_only, condensed=condensed,
                quiet_exit=quiet_exit, laggards_timeout=laggards_timeout,
                wait_for=wait_for
                )

        def multisudo(
            self, command, shell=True, pty=True, combine_stderr=True, user=None,
            dir=None, format=Blank, warn_only=True, condensed=False,
            quiet_exit=True, laggards_timeout=None, wait_for=None
            ):
            def run_sudo():
                return sudo(
                    command, shell, pty, combine_stderr, user, dir, format
                    )
            return self.multirun(
                run_sudo, warn_only=warn_only, condensed=condensed,
                quiet_exit=quiet_exit, laggards_timeout=laggards_timeout,
                wait_for=wait_for
                )

        def multirun(
            self, command, shell=True, pty=True, combine_stderr=True, dir=None,
            format=Blank, warn_only=True, condensed=False, quiet_exit=True,
            laggards_timeout=None, wait_for=None
            ):
            settings_list = self._settings
            if not settings_list:
                return ResponseList.new(settings_list)
            if laggards_timeout:
                if not isinstance(laggards_timeout, int):
                    raise ValueError(
                        "The laggards_timeout parameter must be an int."
                        )
                if isinstance(wait_for, float):
                    if not 0.0 <= wait_for <= 1.0:
                        raise ValueError(
                            "A float wait_for needs to be between 0.0 and 1.0"
                            )
                    wait_for = int(ceil(wait_for * len(settings_list)))
            env.disable_char_buffering = 1
            try:
                return self._multirun(
                    command, settings_list, shell, pty, combine_stderr, dir,
                    format, warn_only, condensed, quiet_exit, laggards_timeout,
                    wait_for
                    )
            finally:
                env.disable_char_buffering = 0

        def _multirun(
            self, command, settings_list, shell, pty, combine_stderr, dir,
            format, warn_only, condensed, quiet_exit, laggards_timeout,
            wait_for
            ):

            callable_command = hasattr(command, '__call__')
            done = 0
            idx = 0
            ctx = self.ctx
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
                    def die(*args):
                        if quiet_exit:
                            output.status = False
                            sys.exit()
                    signal(SIGALRM, die)
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
                            die()
                        try:
                            if callable_command:
                                with settings(
                                    ctx=ctx, warn_only=warn_only,
                                    **settings_list[idx]
                                    ):
                                    try:
                                        response = command()
                                    except Exception, error:
                                        handle_failure(command, warn_only)
                                        response = error
                            else:
                                with settings(
                                    ctx=ctx, warn_only=warn_only,
                                    **settings_list[idx]
                                    ):
                                    response = run(
                                        command, shell, pty, combine_stderr,
                                        dir, format
                                        )
                        except BaseException, error:
                            response = error
                        client = socket(AF_UNIX, SOCK_STREAM)
                        client.connect(socket_path)
                        client.send(dumps((client_id, idx, response)))
                        client.close()


            if laggards_timeout:
                break_early = 0
                responses = [TIMEOUT] * total
                def timeout_handler(*args):
                    raise TimeoutException
                original_alarm_handler = signal(SIGALRM, timeout_handler)
                total_waited = 0.0
            else:
                responses = [None] * total

            if condensed:
                prefix = '[multirun]'
                if env.colors:
                    prefix = env.color_settings['prefix'](prefix)
                stdout = sys.stdout
                if callable_command:
                    command = '%s()' % command.__name__
                else:
                    command = command
                if total < pool_size:
                    print (
                        "%s Running %r on %s hosts" % (prefix, command, total)
                        )
                else:
                    print (
                        "%s Running %r on %s hosts with pool of %s" %
                        (prefix, command, total, pool_size)
                        )
                template = "%s %%s/%s completed ..." % (prefix, total)
                info = template % 0
                written = len(info) + 1
                stdout.write(info)
                stdout.flush()

            while done < total:
                if laggards_timeout:
                    try:
                        if wait_for and done >= wait_for:
                            wait_start = time()
                        alarm(laggards_timeout)
                        conn, addr = server.accept()
                    except TimeoutException:
                        if not wait_for:
                            break_early= 1
                            break
                        if done >= wait_for:
                            break_early = 1
                            break
                        continue
                    else:
                        alarm(0)
                        if wait_for and done >= wait_for:
                            total_waited += time() - wait_start
                            if total_waited > laggards_timeout:
                                break_early = 1
                                break
                else:
                    conn, addr = server.accept()
                stream = []; buffer = stream.append
                while 1:
                    try:
                        data = conn.recv(1024)
                    except socketerror, errmsg:
                        if errmsg.errno in [EAGAIN, EPIPE, EINTR]:
                            continue
                        raise
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
                        "%s Finished on %s" %
                        (prefix, settings_list[resp_idx]['host_string'])
                        )
                    if done == total:
                        info = "%s %s/%s completed successfully!" % (
                            prefix, done, done
                            )
                    else:
                        info = template % done
                    written = len(info) + 1
                    stdout.write(info)
                    stdout.flush()

            if laggards_timeout:
                if break_early:
                    for spec in processes.itervalues():
                        kill(spec[2], SIGTERM)
                    if condensed:
                        stdout.write('\x08' * written)
                        info = "%s %s/%s completed ... laggards discarded!" % (
                            prefix, done, total
                            )
                        stdout.write(info)
                        stdout.flush()
                signal(SIGALRM, original_alarm_handler)

            if condensed:
                stdout.write('\n')
                stdout.flush()

            server.close()
            remove(socket_path)

            return ResponseList.new(settings_list, responses)

    else:

        def multilocal(self, *args, **kwargs):
            abort("multilocal is not supported on this setup")

        def multirun(self, *args, **kwargs):
            abort("multirun is not supported on this setup")

        def multisudo(self, *args, **kwargs):
            abort("multisudo is not supported on this setup")

    def select(self, filter):
        if isinstance(filter, int):
            return ContextRunner(settings=sample(self._settings, count))
        return ContextRunner(settings=filter(self._settings[:]))

    @property
    def settings(self):
        return self._settings[:]

# ------------------------------------------------------------------------------
# Utility API Functions
# ------------------------------------------------------------------------------

def failed(responses):
    """Utility function that returns True if any of the responses failed."""
    return any(isinstance(resp, Exception) or resp.failed for resp in responses)


def succeeded(responses):
    """Utility function that returns True if the responses all succeeded."""
    return all(
        (not isinstance(resp, Exception)) and resp.succeeded
        for resp in responses
        )


def shell(name_or_func=None, single=False):
    """Decorator to register shell builtin commands."""
    if name_or_func:
        if isinstance(name_or_func, basestring):
            name = name_or_func
            func = None
        else:
            name = name_or_func.__name__
            func = name_or_func
    else:
        name = func = None
    if func:
        builtins[name.replace('_', '-')] = func
        if single:
            func.__single__ = 1
        return func
    def __decorate(func):
        builtins[(name or func.__name__).replace('_', '-')] = func
        if single:
            func.__single__ = 1
        return func
    return __decorate

# ------------------------------------------------------------------------------
# Default Shell Builtins
# ------------------------------------------------------------------------------

@shell(single=True)
def info(spec, arg):
    """list the hosts and the current context"""
    print
    print "Context:"
    print
    print "\n".join("   %s" % ctx for ctx in env.ctx)
    print
    print "Hosts:"
    print
    for setting in env().settings:
        print "  ", setting['host_string']
    print


@shell(single=True)
def cd(spec, arg):
    """change to a new working directory"""
    arg = arg.strip()
    if arg:
        if isabs(arg):
            spec.dir = arg
        elif arg.startswith('~'):
            spec.dir = expanduser(arg)
        else:
            if spec.dir:
                spec.dir = join(spec.dir, arg)
            else:
                spec.dir = join(getcwd(), arg)
        spec.dir = normpath(spec.dir)
        print "Switched to:", spec.dir
    else:
        spec.dir = None


@shell('local', single=True)
def builtin_local(spec, arg):
    """run the command locally"""
    local(arg, capture=0, dir=spec.dir, format=spec.format)


@shell('sudo', single=True)
def builtin_sudo(spec, arg):
    """run the sudoed command on remote hosts"""
    return sudo(
        arg, spec.shell, spec.pty, spec.combine_stderr, None, spec.dir,
        spec.format
        )


@shell(single=True)
def toggle_format(spec, arg):
    """toggle string formatting support"""
    format = spec.format
    if format is Blank:
        format = env.format
    if format:
        spec.format = False
        print "Formatting disabled."
    else:
        spec.format = True
        print "Formatting enabled."


@shell(single=True)
def foo(spec, arg):
    1/0

@shell(single=True)
def multilocal(spec, arg):
    """run the command in parallel locally for each host"""
    def run_local():
        return local(arg, capture=0, dir=spec.dir, format=spec.format)
    env().multirun(
        run_local, spec.shell, spec.pty, spec.combine_stderr, spec.dir,
        spec.format, quiet_exit=1
        )


@shell(single=True)
def multirun(spec, arg):
    """run the command in parallel on the various hosts"""
    env().multirun(
        arg, spec.shell, spec.pty, spec.combine_stderr, spec.dir, spec.format,
        quiet_exit=1
        )


@shell(single=True)
def multisudo(spec, arg):
    """run the sudoed command in parallel on the various hosts"""
    def run_sudo():
        return sudo(
            arg, spec.shell, spec.pty, spec.combine_stderr, None, spec.dir,
            spec.format
            )
    env().multirun(
        run_sudo, spec.shell, spec.pty, spec.combine_stderr, spec.dir,
        spec.format, quiet_exit=1
        )


@shell(single=True)
def help(spec, arg):
    """display the list of available builtin commands"""
    max_len = max(len(x) for x in builtins)
    max_width = 80 - max_len - 5
    print
    print "Available Builtins:"
    print
    for builtin in sorted(builtins):
        padding = (max_len - len(builtin)) * ' '
        docstring = builtins[builtin].__doc__ or ''
        if len(docstring) > max_width:
            docstring = docstring[:max_width-3] + "..."
        print "  %s%s   %s" % (padding, builtin, docstring)
    print

# ------------------------------------------------------------------------------
# Readline Completer
# ------------------------------------------------------------------------------

binaries_on_path = []

def get_binaries_on_path():
    env_path = environ.get('PATH')
    if not env_path:
        return
    append = binaries_on_path.append
    for path in env_path.split(pathsep):
        path = path.strip()
        if not path:
            continue
        if not isdir(path):
            continue
        for file in listdir(path):
            file_path = join(path, file)
            if access(file_path, X_OK):
                append(file)
    binaries_on_path.sort()


def complete(text, state, matches=[], binaries={}):
    if not state:
        if text.startswith('.'):
            text = text[1:]
            matches[:] = [
                '.' + builtin + ' '
                for builtin in builtins if builtin.startswith(text)
                ]
        elif text.startswith('{'):
            text = text[1:]
            matches[:] = [
                '{' + prop + '}'
                for prop in env if prop.startswith(text)
                ]
        else:
            if not binaries_on_path:
                get_binaries_on_path()
            matches[:] = []; append = matches.append
            for file in binaries_on_path:
                if file.startswith(text):
                    append(file)
                else:
                    if matches:
                        break
    try:
        return matches[state]
    except IndexError:
        return


try:
    import readline
except ImportError:
    readline = None
else:
    readline.set_completer_delims(' \t\n')
    readline.set_completer(complete)
    readline.parse_and_bind('tab: complete')
