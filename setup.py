#! /usr/bin/env python

import sys

from distutils.core import Extension, setup

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
