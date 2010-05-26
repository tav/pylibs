#! /usr/bin/env python

import sys

from distutils.core import Extension, setup
from os.path import dirname, join as join_path, realpath

from pyutil.env import run_command

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

ssl_path = join_path(pylibs_path, 'ssl')

run_command(
    [sys.executable, join_path(ssl_path, 'setup.py'), 'build_ext', '-i'],
    exit_on_error=True, cwd=join_path(ssl_path), redirect_stdout=False,
    redirect_stderr=False
    )
