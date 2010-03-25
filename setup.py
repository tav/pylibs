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

pycrypto_path = join_path(dirname(realpath(__file__)), 'pycrypto')

run_command(
    [sys.executable, join_path(pycrypto_path, 'setup.py'), 'build_ext', '-i'],
    exit_on_error=True, cwd=join_path(pycrypto_path), redirect_stdout=False,
    redirect_stderr=False
    )
