"""
This module contains Fab's `main` method plus related subroutines.

`main` is executed as the command line ``fab`` program and takes care of
parsing options and commands, loading the user settings file, loading a
fabfile, and executing the commands given.

The other callables defined in this module are internal only. Anything useful
to individuals leveraging Fabric as a library, should be kept elsewhere.
"""

import os
import sys

from fnmatch import fnmatch
from optcomplete import ListCompleter, autocomplete
from optparse import OptionParser
from os.path import dirname, expanduser, join, realpath

from fabric import api
from fabric.context_managers import settings
from fabric.contrib import console, files, project
from fabric.network import interpret_host_string, disconnect_all
from fabric.state import AttributeDict, commands, env, env_options, output
from fabric.utils import abort, indent, puts

from yaml import safe_load as load_yaml


# One-time calculation of "all internal callables" to avoid doing this on every
# check of a given fabfile callable (in is_task()).
_modules = [api, project, files, console]
_internals = reduce(lambda x, y: x + filter(callable, vars(y).values()),
    _modules,
    []
)

HOOKS = {}
DISABLED_HOOKS = []
ENABLED_HOOKS = []


def hook(*names):
    def register(func):
        for name in names:
            name = name.replace('_', '-')
            if name not in HOOKS:
                HOOKS[name] = []
            HOOKS[name].append(func)
        return func
    return register


def get_hooks(name, disabled=False):
    name = name.replace('_', '-')
    for pattern in DISABLED_HOOKS:
        if fnmatch(name, pattern):
            disabled = 1
    for pattern in ENABLED_HOOKS:
        if fnmatch(name, pattern):
            disabled = 0
    if disabled:
        return []
    return HOOKS.get(name, [])


def call_hooks(name, *args, **kwargs):
    name = name.replace('_', '-')
    prev_hook = env.hook
    env.hook = name
    try:
        for hook in get_hooks(name):
            hook(*args, **kwargs)
    finally:
        env.hook = prev_hook


api.hook = hook
hook.get = get_hooks
hook.call = call_hooks
hook.registry = HOOKS


def load_settings(path):
    """
    Take given file path and return dictionary of any key=value pairs found.

    Usage docs are in docs/usage/fab.rst, in "Settings files."
    """
    if os.path.exists(path):
        comments = lambda s: s and not s.startswith("#")
        settings = filter(comments, open(path, 'r'))
        return dict((k.strip(), v.strip()) for k, _, v in
            [s.partition('=') for s in settings])
    # Handle nonexistent or empty settings file
    return {}


def _is_package(path):
    """
    Is the given path a Python package?
    """
    return (
        os.path.isdir(path)
        and os.path.exists(os.path.join(path, '__init__.py'))
    )


def find_fabfile():
    """
    Attempt to locate a fabfile, either explicitly or by searching parent dirs.

    Usage docs are in docs/usage/fabfiles.rst, in "Fabfile discovery."
    """
    # Obtain env value
    names = [env.fabfile]
    # Create .py version if necessary
    if not names[0].endswith('.py'):
        names += [names[0] + '.py']
    # Does the name contain path elements?
    if os.path.dirname(names[0]):
        # If so, expand home-directory markers and test for existence
        for name in names:
            expanded = os.path.expanduser(name)
            if os.path.exists(expanded):
                if name.endswith('.py') or _is_package(expanded):
                    return os.path.abspath(expanded)
    else:
        # Otherwise, start in cwd and work downwards towards filesystem root
        path = '.'
        # Stop before falling off root of filesystem (should be platform
        # agnostic)
        while os.path.split(os.path.abspath(path))[1]:
            for name in names:
                joined = os.path.join(path, name)
                if os.path.exists(joined):
                    if name.endswith('.py') or _is_package(joined):
                        return os.path.abspath(joined)
            path = os.path.join('..', path)
    # Implicit 'return None' if nothing was found


def is_task(tup):
    """
    Takes (name, object) tuple, returns True if it's a non-Fab public callable.
    """
    name, func = tup
    return (
        callable(func)
        and (func not in _internals)
        and not name.startswith('_')
    )

def load_fabfile(path):
    """
    Import given fabfile path and return (docstring, callables).

    Specifically, the fabfile's ``__doc__`` attribute (a string) and a
    dictionary of ``{'name': callable}`` containing all callables which pass
    the "is a Fabric task" test.
    """
    # Get directory and fabfile name
    directory, fabfile = os.path.split(path)
    # If the directory isn't in the PYTHONPATH, add it so our import will work
    added_to_path = False
    index = None
    if directory not in sys.path:
        sys.path.insert(0, directory)
        added_to_path = True
    # If the directory IS in the PYTHONPATH, move it to the front temporarily,
    # otherwise other fabfiles -- like Fabric's own -- may scoop the intended
    # one.
    else:
        i = sys.path.index(directory)
        if i != 0:
            # Store index for later restoration
            index = i
            # Add to front, then remove from original position
            sys.path.insert(0, directory)
            del sys.path[i + 1]
    # Perform the import (trimming off the .py)
    imported = __import__(os.path.splitext(fabfile)[0])
    # Remove directory from path if we added it ourselves (just to be neat)
    if added_to_path:
        del sys.path[0]
    # Put back in original index if we moved it
    if index is not None:
        sys.path.insert(index + 1, directory)
        del sys.path[0]
    # Filter down to our two-tuple
    if not api.task.used:
        tasks = dict(filter(is_task, vars(imported).items()))
    else:
        tasks = dict(
            (var, obj) for var, obj in vars(imported).items()
            if hasattr(obj, '__fabtask__')
            )
    # Support for stages
    stages = os.environ.get('FAB_STAGES', env.get('stages'))
    if stages:
        if isinstance(stages, basestring):
            stages = [stage.strip() for stage in stages.split(',')]
        env.stages = stages
        for stage in stages:
            set_env_stage_command(tasks, stage)
    return imported.__doc__, tasks


def set_env_stage_command(tasks, stage):
    if stage in tasks:
        return
    def set_stage():
        """Set the environment to %s.""" % stage
        puts('env.stage = %s' % stage, 'system')
        env.stage = stage
        config_file = env.config_file
        if config_file:
            if not isinstance(config_file, basestring):
                config_file = '%s.yaml'
            try:
                env.config_file = config_file % stage
            except TypeError:
                env.config_file = config_file
    set_stage.__hide__ = 1
    set_stage.__name__ = stage
    tasks[stage] = set_stage
    return set_stage


def parse_options():
    """
    Handle command-line options with optparse.OptionParser.

    Return list of arguments, largely for use in `parse_arguments`.
    """
    #
    # Initialize
    #

    parser = OptionParser(usage="fab [options] <command>[:arg1,arg2=val2,host=foo,hosts='h1;h2',...] ...")

    #
    # Define options that don't become `env` vars (typically ones which cause
    # Fabric to do something other than its normal execution, such as --version)
    #

    # Version number (optparse gives you --version but we have to do it
    # ourselves to get -V too. sigh)
    parser.add_option('-V', '--version',
        action='store_true',
        dest='show_version',
        default=False,
        help="show program's version number and exit"
    )

    # List Fab commands found in loaded fabfiles/source files
    parser.add_option('-l', '--list',
        action='store_true',
        dest='list_commands',
        default=False,
        help="print list of possible commands and exit"
    )

    # Like --list, but text processing friendly
    parser.add_option('--shortlist',
        action='store_true',
        dest='shortlist',
        default=False,
        help="print non-verbose list of possible commands and exit"
    )

    # Display info about a specific command
    parser.add_option('-d', '--display',
        metavar='COMMAND',
        help="print detailed info about a given command and exit"
    )

    # Hooks related options
    parser.add_option(
        '--disable-hooks', metavar='PATTERNS',
        help="disable all matching hooks"
        )

    parser.add_option(
        '--enable-hooks', metavar='PATTERNS',
        help="enable all matching hooks (overrides --disable-hooks)"
        )

    #
    # Add in options which are also destined to show up as `env` vars.
    #

    for option in env_options:
        parser.add_option(option)

    #
    # Finalize
    #

    # Return three-tuple of parser + the output from parse_args (opt obj, args)
    opts, args = parser.parse_args()
    return parser, opts, args


def _command_names():
    return sorted(commands.keys())


def list_commands(docstring):
    """
    Print all found commands/tasks, then exit. Invoked with ``-l/--list.``

    If ``docstring`` is non-empty, it will be printed before the task list.
    """
    if docstring:
        trailer = "\n" if not docstring.endswith("\n") else ""
        print(docstring + trailer)
    print("Available commands:\n")
    # Want separator between name, description to be straight col
    max_len = reduce(lambda a, b: max(a, len(b)), commands.keys(), 0)
    sep = '  '
    trail = '...'
    for name in _command_names():
        output = None
        # Print first line of docstring
        func = commands[name]
        if hasattr(func, '__hide__'):
            continue
        name = name.replace('_', '-')
        if func.__doc__:
            lines = filter(None, func.__doc__.splitlines())
            first_line = lines[0].strip()
            # Truncate it if it's longer than N chars
            size = 75 - (max_len + len(sep) + len(trail))
            if len(first_line) > size:
                first_line = first_line[:size] + trail
            output = name.ljust(max_len) + sep + first_line
        # Or nothing (so just the name)
        else:
            output = name
        print(indent(output))
    print
    if 'stages' in env:
        print 'Available environments:'
        print
        for stage in env.stages:
            print '    %s' % stage
        print
    call_hooks('listing.display')
    sys.exit(0)


def shortlist():
    """
    Print all task names separated by newlines with no embellishment.
    """
    print("\n".join(cmd.replace('_', '-') for cmd  in _command_names()))
    sys.exit(0)


def display_command(command):
    """
    Print command function's docstring, then exit. Invoked with -d/--display.
    """
    # Sanity check
    command = command.replace('-', '_')
    cmd_string = command.replace('_', '-')
    if command not in commands:
        abort("Command '%s' not found, exiting." % cmd_string)
    cmd = commands[command]
    # Print out nicely presented docstring if found
    if cmd.__doc__:
        print("Displaying detailed information for command '%s':" % cmd_string)
        print('')
        print(indent(cmd.__doc__, strip=True))
        print('')
    # Or print notice if not
    else:
        print("No detailed information available for command '%s':" % cmd_string)
    sys.exit(0)


def _escape_split(sep, argstr):
    """
    Allows for escaping of the separator: e.g. task:arg='foo\, bar'

    It should be noted that the way bash et. al. do command line parsing, those
    single quotes are required.
    """
    escaped_sep = r'\%s' % sep

    if escaped_sep not in argstr:
        return argstr.split(sep)

    before, _, after = argstr.partition(escaped_sep)
    startlist = before.split(sep) # a regular split is fine here
    unfinished = startlist[-1]
    startlist = startlist[:-1]

    # recurse because there may be more escaped separators
    endlist = _escape_split(sep, after)

    # finish building the escaped value. we use endlist[0] becaue the first
    # part of the string sent in recursion is the rest of the escaped value.
    unfinished += sep + endlist[0]

    return startlist + [unfinished] + endlist[1:] # put together all the parts


def parse_arguments(arguments):
    """
    Parse string list into list of tuples: command, args, kwargs, hosts, roles.

    See docs/usage/fab.rst, section on "per-task arguments" for details.
    """
    cmds = []
    env_update = {}
    idx = 0
    for cmd in arguments:
        args = []
        kwargs = {}
        context = None
        hosts = []
        roles = []
        if cmd.startswith('+'):
            if ':' in cmd:
                name, value = cmd[1:].split(':', 1)
                env_update[name] = value
            else:
                env_update[cmd[1:]] = True
            continue
        elif cmd.startswith('@') and idx:
            ctx = (cmd[1:],)
            existing = cmds[idx-1][3]
            if existing:
                new = list(existing)
                new.extend(ctx)
                ctx = tuple(new)
            cmds[idx-1][3] = ctx
            continue
        if ':' in cmd:
            cmd, argstr = cmd.split(':', 1)
            for pair in _escape_split(',', argstr):
                k, _, v = pair.partition('=')
                if _:
                    # Catch, interpret host/hosts/role/roles kwargs
                    if k in ['host', 'hosts', 'role', 'roles']:
                        if k == 'host':
                            hosts = [v.strip()]
                        elif k == 'hosts':
                            hosts = [x.strip() for x in v.split(';')]
                        elif k == 'role':
                            roles = [v.strip()]
                        elif k == 'roles':
                            roles = [x.strip() for x in v.split(';')]
                    # Otherwise, record as usual
                    else:
                        kwargs[k] = v
                else:
                    args.append(k)
        idx += 1
        cmd = cmd.replace('-', '_')
        cmds.append([cmd, args, kwargs, context, hosts, roles])
    return cmds, env_update


def parse_remainder(arguments):
    """
    Merge list of "remainder arguments" into a single command string.
    """
    return ' '.join(arguments)


def _merge(hosts, roles):
    """
    Merge given host and role lists into one list of deduped hosts.
    """
    # Abort if any roles don't exist
    bad_roles = [x for x in roles if x not in env.roledefs]
    if bad_roles:
        abort("The following specified roles do not exist:\n%s" % (
            indent(bad_roles)
        ))

    # Look up roles, turn into flat list of hosts
    role_hosts = []
    for role in roles:
        value = env.roledefs[role]
        # Handle "lazy" roles (callables)
        if callable(value):
            value = value()
        role_hosts += value
    # Return deduped combo of hosts and role_hosts
    return list(set(hosts + role_hosts))


def get_hosts(command, cli_hosts, cli_roles):
    """
    Return the host list the given command should be using.

    See :ref:`execution-model` for detailed documentation on how host lists are
    set.
    """
    # Command line per-command takes precedence over anything else.
    if cli_hosts or cli_roles:
        return _merge(cli_hosts, cli_roles)
    # Decorator-specific hosts/roles go next
    func_hosts = getattr(command, 'hosts', [])
    func_roles = getattr(command, 'roles', [])
    if func_hosts or func_roles:
        return _merge(func_hosts, func_roles)
    # Finally, the env is checked (which might contain globally set lists from
    # the CLI or from module-level code). This will be the empty list if these
    # have not been set -- which is fine, this method should return an empty
    # list if no hosts have been set anywhere.
    return _merge(env['hosts'], env['roles'])


def update_output_levels(show, hide):
    """
    Update state.output values as per given comma-separated list of key names.

    For example, ``update_output_levels(show='debug,warnings')`` is
    functionally equivalent to ``state.output['debug'] = True ;
    state.output['warnings'] = True``. Conversely, anything given to ``hide``
    sets the values to ``False``.
    """
    if show:
        for key in show.split(','):
            output[key] = True
    if hide:
        for key in hide.split(','):
            output[key] = False


def log_execution(name, host=None):
    # Log to stdout
    if output.running:
        msg = "running task: %s" % name
        if host:
            prefix = '[%s] ' % host
            if env.colors:
                prefix = env.color_settings['host_prefix'](prefix)
        else:
            prefix = '[system] '
            if env.colors:
                prefix = env.color_settings['prefix'](prefix)
        print(prefix + msg)

def execute_command(spec, commands):
    """Execute the given spec from the commands mapping."""
    name, args, kwargs, ctx, cli_hosts, cli_roles = spec
    # Get callable by itself
    command = commands[name]
    # Set current command name (used for some error messages)
    env.command = name
    # Run with context, if any are specified
    if not ctx:
        ctx = getattr(command, '__ctx__', None)
    if ctx:
        log_execution(name)
        with settings(ctx=ctx):
            command(*args, **kwargs)
        return
    # Set host list (also copy to env)
    env.all_hosts = hosts = get_hosts(
        command, cli_hosts, cli_roles)
    # If hosts found, execute the function on each host in turn
    for host in hosts:
        # Preserve user
        prev_user = env.user
        # Split host string and apply to env dict
        interpret_host_string(host)
        log_execution(name, host)
        # Actually run command
        command(*args, **kwargs)
        # Put old user back
        env.user = prev_user
    # If no hosts found, assume local-only and run once
    if not hosts:
        command(*args, **kwargs)


def main():
    """
    Main command-line execution loop.
    """
    try:
        # Parse command line options
        parser, options, arguments = parse_options()

        # Handle regular args vs -- args
        arguments = parser.largs
        remainder_arguments = parser.rargs

        # Update env with any overridden option values
        # NOTE: This needs to remain the first thing that occurs
        # post-parsing, since so many things hinge on the values in env.
        for option in env_options:
            env[option.dest] = getattr(options, option.dest)

        # Handle --hosts, --roles (comma separated string => list)
        for key in ['hosts', 'roles']:
            if key in env and isinstance(env[key], str):
                env[key] = env[key].split(',')

        # Handle output control level show/hide
        update_output_levels(show=options.show, hide=options.hide)

        # Handle version number option
        if options.show_version:
            print("Fabric %s" % env.version)
            sys.exit(0)

        # Load settings from user settings file, into shared env dict.
        env.update(load_settings(env.rcfile))

        # Find local fabfile path or abort
        fabfile = find_fabfile()
        if not fabfile and not remainder_arguments:
            abort("Couldn't find any fabfiles!")

        # Store absolute path to fabfile in case anyone needs it
        env.real_fabfile = fabfile

        # Load fabfile (which calls its module-level code, including
        # tweaks to env values) and put its commands in the shared commands
        # dict
        if fabfile:
            docstring, callables = load_fabfile(fabfile)
            commands.update(callables)

        # Autocompletion support
        autocomplete_items = [cmd.replace('_', '-') for cmd in commands]
        if 'autocomplete' in env:
            autocomplete_items += env.autocomplete

        autocomplete(parser, ListCompleter(autocomplete_items))

        # Handle hooks related options
        _disable_hooks = options.disable_hooks
        _enable_hooks = options.enable_hooks

        if _disable_hooks:
            for _hook in _disable_hooks.strip().split():
                DISABLED_HOOKS.append(_hook.strip())

        if _enable_hooks:
            for _hook in _enable_hooks.strip().split():
                ENABLED_HOOKS.append(_hook.strip())

        # Handle the non-execution flow
        if not arguments and not remainder_arguments:

            # Non-verbose command list
            if options.shortlist:
                shortlist()

            # Handle show (command-specific help) option
            if options.display:
                display_command(options.display)

            # Else, show the list of commands and exit
            list_commands(docstring)

        # Now that we're settled on a fabfile, inform user.
        if output.debug:
            if fabfile:
                print("Using fabfile '%s'" % fabfile)
            else:
                print("No fabfile loaded -- remainder command only")

        # Parse arguments into commands to run (plus args/kwargs/hosts)
        commands_to_run, env_update = parse_arguments(arguments)
        env.update(env_update)

        # Parse remainders into a faux "command" to execute
        remainder_command = parse_remainder(remainder_arguments)

        # Figure out if any specified task names are invalid
        unknown_commands = []
        for tup in commands_to_run:
            if tup[0] not in commands:
                unknown_commands.append(tup[0])

        # Abort if any unknown commands were specified
        if unknown_commands:
            abort("Command(s) not found:\n%s" \
                % indent(unknown_commands))

        # Generate remainder command and insert into commands, commands_to_run
        if remainder_command:
            r = '<remainder>'
            commands[r] = lambda: api.run(remainder_command)
            commands_to_run.append((r, [], {}, [], []))

        if output.debug:
            names = ", ".join(x[0] for x in commands_to_run)
            print("Commands to run: %s" % names)

        call_hooks('commands.before', commands, commands_to_run)

        # Initialse context runner
        env()

        # Initialise the default stage if none are given as the first command.
        if 'stages' in env:
            if commands_to_run[0][0] not in env.stages:
                execute_command(
                    (env.stages[0], (), {}, None, None, None), commands
                    )
            else:
                execute_command(commands_to_run.pop(0), commands)

        if env.config_file:
            config_path = realpath(expanduser(env.config_file))
            config_path = join(dirname(fabfile), config_path)
            config_file = open(config_path, 'rb')
            config = load_yaml(config_file.read())
            if not config:
                env.config = AttributeDict()
            elif not isinstance(config, dict):
                abort("Invalid config file found at %s" % config_path)
            else:
                env.config = AttributeDict(config)
            config_file.close()

        call_hooks('config.loaded')
        first_time_env_call = 1

        # At this point all commands must exist, so execute them in order.
        for spec in commands_to_run:
            execute_command(spec, commands)

        # If we got here, no errors occurred, so print a final note.
        if output.status:
            msg = "\nDone."
            if env.colors:
                msg = env.color_settings['finish'](msg)
            print(msg)

    except SystemExit:
        # a number of internal functions might raise this one.
        raise
    except KeyboardInterrupt:
        if output.status:
            msg = "\nStopped."
            if env.colors:
                msg = env.color_settings['finish'](msg)
            print >> sys.stderr, msg
        sys.exit(1)
    except:
        sys.excepthook(*sys.exc_info())
        # we might leave stale threads if we don't explicitly exit()
        sys.exit(1)
    finally:
        call_hooks('commands.after')
        disconnect_all()
    sys.exit(0)
