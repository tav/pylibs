# Public Domain (-) 2011 The Ampify Authors.
# See the Ampify UNLICENSE file for details.

"""Settings loader."""

from fnmatch import fnmatch

from fabric.network import host_regex
from fabric.state import env


cache = {}
default = {}
hostinfo = {}
hostpatterninfo = {}
hostpatterns = []


def get_settings(
    contexts, env=env, cache=cache, default=default, hostinfo=hostinfo,
    hostpatterninfo=hostpatterninfo, hostpatterns=hostpatterns
    ):
    """Return a sequence of host/settings for the given contexts tuple."""

    # Exit early for null contexts.
    if not contexts:
        return []

    # Check the cache.
    if contexts in cache:
        return cache[contexts]

    # Mimick @hosts-like behaviour when there's no env.config.
    if 'config' not in env:
        responses = []; out = responses.append
        for host in contexts:
            if host and (('.' in host) or (host == 'localhost')):
                resp = {'host_string': host}
                info = host_regex.match(host).groupdict()
                resp['host'] = info['host']
                resp['port'] = info['port'] or '22'
                resp['user'] = info['user'] or env.get('user')
                out(resp)
        return cache.setdefault(contexts, responses)

    # Save env.config to a local parameter to avoid repeated lookup.
    config = env.config

    # Set a marker to handle the first time.
    if not cache:

        cache['_init'] = 1

        # Grab the root default settings.
        if 'default' in config:
            default.update(config.default)

        # Grab any host specific settings.
        if 'hostinfo' in config:
            for host, info in config.hostinfo.items():
                if ('*' in host) or ('?' in host) or ('[' in host):
                    hostpatterninfo[host] = info
                else:
                    hostinfo[host] = info
            if hostpatterninfo:
                hostpatterns[:] = sorted(hostpatterninfo)

        def get_host_info(context, init=None):
            resp = default.copy()
            if init:
                resp.update(init)
            info = host_regex.match(context).groupdict()
            host = info['host']
            for pattern in hostpatterns:
                if fnmatch(host, pattern):
                    resp.update(hostpatterninfo[pattern])
                if fnmatch(context, pattern):
                    resp.update(hostpatterninfo[pattern])
            if host in hostinfo:
                resp.update(hostinfo[host])
            if context in hostinfo:
                resp.update(hostinfo[context])
            resp['host'] = host
            resp['host_string'] = context
            if info['port']:
                resp['port'] = info['port']
            elif 'port' not in resp:
                resp['port'] = '22'
            if info['user']:
                resp['user'] = info['user']
            elif 'user' not in resp:
                resp['user'] = env.user
            return resp

        get_settings.get_host_info = get_host_info

    else:
        get_host_info = get_settings.get_host_info

    # Loop through the contexts gathering host/settings.
    responses = []; out = responses.append
    for context in contexts:

        # Handle composite contexts.
        if '/' in context:
            context, hosts = context.split('/', 1)
            hosts = hosts.split(',')
            base = config[context].copy()
            additional = {}
            for _host in base.pop('hosts', []):
                if isinstance(_host, dict):
                    _host, _additional = _host.items()[0]
                    additional[_host] = _additional
            for host in hosts:
                if host in additional:
                    resp = get_host_info(host, base)
                    resp.update(additional[host])
                    out(resp)
                else:
                    out(get_host_info(host, base))

        # Handle hosts.
        elif ('.' in context) or (context == 'localhost'):
            out(get_host_info(context))

        else:
            base = config[context].copy()
            hosts = base.pop('hosts')
            for host in hosts:
                if isinstance(host, basestring):
                    out(get_host_info(host, base))
                else:
                    if len(host) > 1:
                        raise ValueError(
                            "More than 1 host found in config:\n\n%r\n"
                            % host.items()
                            )
                    host, additional = host.items()[0]
                    resp = get_host_info(host, base)
                    resp.update(additional)
                    out(resp)

    return cache.setdefault(contexts, responses)
