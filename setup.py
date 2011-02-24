#! /usr/bin/env python

# Public Domain (-) 2010-2011 The Ampify Authors.
# See the Ampify UNLICENSE file for details.

import sys

from distutils.core import Extension, setup
from os.path import dirname, join as join_path, realpath

try:
    from pyutil.env import run_command
except ImportError:
    from subprocess import call
    def run_command(args, cwd, **kwargs):
        retcode = call(args, cwd=cwd)
        if retcode and kwargs.pop('exit_on_error', None):
            sys.exit(1)

# ------------------------------------------------------------------------------
# the extensions
# ------------------------------------------------------------------------------

extensions = [
    Extension("simplejson._speedups", ["simplejson/_speedups.c"]),
    Extension('greenlet', ['greenlet/greenlet.c'], include_dirs=['greenlet'])
    ]

# ------------------------------------------------------------------------------
# run setup
# ------------------------------------------------------------------------------

if not sys.argv[1:]:
    sys.argv.extend(['build_ext', '-i'])

setup(
    name="pylibs",
    version="git",
    description="A collection of Python libraries",
    ext_modules=extensions,
    )

pylibs_path = dirname(realpath(__file__))
packages_path = ['pycrypto']

if sys.version_info < (2, 6):
    packages_path.append('pyssl')

for path in packages_path:
    path = join_path(pylibs_path, path)
    run_command(
        [sys.executable, join_path(path, 'setup.py')] + sys.argv[1:],
        exit_on_error=True, cwd=path, redirect_stdout=False,
        redirect_stderr=False
        )
