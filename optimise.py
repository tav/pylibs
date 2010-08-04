# Changes to this file by The Ampify Authors are according to the
# Public Domain license that can be found in the root LICENSE file.

# adapted from http://code.activestate.com/recipes/277940/
# thanks Raymond Hettinger and Paul Cannon!

"""
===========================
Automatic Code Optimisation
===========================

When hand optimising functions, it is typical to include lines like:

    >>> def some_function():
    ...
    ...     _len = len
    ...     _int = int
    ...
    ...     # do something with _len and _int ...

This is useful in functions with tight loops. By assigning to variables in the
local namespace, code can avoid wasting time doing repeated global namespace
lookups.

This module provides an optimiser which binds variables like ``len`` to local
constants. The speed-up is substantial -- trading one or more dictionary-based
namespace lookups for a single C-speed array lookup -- and keeps the original
code free of clutter like _len=len.

You use ``optimise`` like a normal decorator:

    >>> import random

    >>> @optimise(verbose=True)
    ... def sample(population, k):
    ...     '''Return k unique random elements from a population sequence.'''
    ...
    ...     if not isinstance(population, (list, tuple, str)):
    ...         raise TypeError('Cannot handle type', type(population))
    ...
    ...     n = len(population)
    ...
    ...     if not 0 <= k <= n:
    ...         raise ValueError('Sample larger than population')
    ...
    ...     result = [None] * k
    ...     pool = list(population)
    ...
    ...     for i in xrange(k):         # invariant:  non-selected at [0,n-i)
    ...         j = int(random.random() * (n-i))
    ...         result[i] = pool[j]
    ...         pool[j] = pool[n-i-1]   # move non-selected item into vacancy
    ...
    ...     return result
    isinstance --> <built-in function isinstance>
    list --> <type 'list'>
    tuple --> <type 'tuple'>
    str --> <type 'str'>
    TypeError --> <type 'exceptions.TypeError'>
    type --> <type 'type'>
    len --> <built-in function len>
    ValueError --> <type 'exceptions.ValueError'>
    list --> <type 'list'>
    xrange --> <type 'xrange'>
    int --> <type 'int'>
    random --> <module 'random' from '...'>
    new folded constant: (<type 'list'>, <type 'tuple'>, <type 'str'>)
    new folded constant: <built-in method random of Random object at ...>

Variables like ``random`` from the global namespace and ``len``, ``ValueError``,
``list``, ``xrange``, and ``int`` from the builtin namespace are replaced with
constants. If ``random`` had not already been imported at binding time, then it
would have been left as a runtime global lookup.

Constant binding is especially useful for:

* Functions with many references to global constants (sre_compile for example).
* Functions relying on builtins (such as len(), True, and False).
* Functions that raise or catch exceptions (such as IndexError).
* Recursive functions (to optimise the function's reference to itself).
* Functions that call other functions defined in the same module.
* Functions accessing names imported into the global namespace (i.e. from random
  import random).

After binding globals to constants, the decorator makes a second pass and folds
constant attribute lookups and tuples of constants into single constants. The
first frequently occurs with module references (such as string.hexdigits). The
second commonly occurs in for-loops and conditionals using the ``in`` operator
such as: for x in ('a','b','c').

Binding should be applied selectively to those functions where speed is
important and dynamic updates are not desired (i.e. the globals do not change).

In more dynamic environments, a more conservative approach is to set
builtin_only to True so that only the builtins get optimised (this includes
functions like len(), exceptions like IndexError, and constants like True or
False). Alternatively, individual variable names can be added to a stoplist so
that the function knows to leave them unchanged.

Sine optimise() works like other function decorators such as classmethod() and
staticmethod(), it can be used inside a class definition to optimise methods.

When optimising recursive functions, you need to use the following approach
instead of using the @optimise decorator::

    sample = _make_constants(sample, verbose=True)

A utility ``optimise_all`` function is also provided to automatically decorate
every function and method in a module or class. Call optimise_all(my_class)
after the class definition or put optimise_all(sys.modules[__name__]) as the
last line in a module needing optimisation.

"""

import __builtin__
import logging
import sys

from opcode import opmap, HAVE_ARGUMENT, EXTENDED_ARG
from types import FunctionType, ClassType, CodeType

# ------------------------------------------------------------------------------
# some konstants
# ------------------------------------------------------------------------------

globals().update(opmap)

def _make_constants(
    function, builtins=None, builtin_only=False, stoplist=(), constant_fold=True,
    verbose=False,
    ):
    """Return the given ``function`` with its constants folded."""

    if verbose == 2:
        logging.info(
            "# OPTIMISING : %s.%s", function.__module__, function.__name__
            )

    co = function.func_code
    newcode = map(ord, co.co_code)
    newconsts = list(co.co_consts)
    names = co.co_names
    codelen = len(newcode)

    if builtins:
        env = vars(builtins).copy()
    else:
        env = vars(__builtin__).copy()

    if builtin_only:
        stoplist = dict.fromkeys(stoplist)
        stoplist.update(function.func_globals)
    else:
        env.update(function.func_globals)

    # first pass converts global lookups into constants

    i = 0

    while i < codelen:
        opcode = newcode[i]
        if opcode in (EXTENDED_ARG, STORE_GLOBAL):
            # for simplicity, only optimise common cases
            logging.info("\n\nFound opcode\n\n")
            return function
        if opcode == LOAD_GLOBAL:
            oparg = newcode[i+1] + (newcode[i+2] << 8)
            name = co.co_names[oparg]
            if name in env and name not in stoplist:
                value = env[name]
                for pos, v in enumerate(newconsts):
                    if v is value:
                        break
                else:
                    pos = len(newconsts)
                    newconsts.append(value)
                newcode[i] = LOAD_CONST
                newcode[i+1] = pos & 0xFF
                newcode[i+2] = pos >> 8
                if verbose:
                    logging.info("%s --> %s", name, value)
        i += 1
        if opcode >= HAVE_ARGUMENT:
            i += 2

    # second pass folds tuples of constants and constant attribute lookups

    if constant_fold:

        i = 0

        while i < codelen:

            newtuple = []
            while newcode[i] == LOAD_CONST:
                oparg = newcode[i+1] + (newcode[i+2] << 8)
                newtuple.append(newconsts[oparg])
                i += 3

            opcode = newcode[i]
            if not newtuple:
                i += 1
                if opcode >= HAVE_ARGUMENT:
                    i += 2
                continue

            if opcode == LOAD_ATTR:
                obj = newtuple[-1]
                oparg = newcode[i+1] + (newcode[i+2] << 8)
                name = names[oparg]
                try:
                    value = getattr(obj, name)
                except AttributeError:
                    continue
                deletions = 1

            elif opcode == BUILD_TUPLE:
                oparg = newcode[i+1] + (newcode[i+2] << 8)
                if oparg != len(newtuple):
                    continue
                deletions = len(newtuple)
                value = tuple(newtuple)

            else:
                continue

            reljump = deletions * 3
            newcode[i-reljump] = JUMP_FORWARD
            newcode[i-reljump+1] = (reljump-3) & 0xFF
            newcode[i-reljump+2] = (reljump-3) >> 8

            n = len(newconsts)
            newconsts.append(value)
            newcode[i] = LOAD_CONST
            newcode[i+1] = n & 0xFF
            newcode[i+2] = n >> 8

            i += 3

            if verbose:
                logging.info("New folded constant: %s", value)

    codestr = ''.join(map(chr, newcode))
    codeobj = CodeType(
        co.co_argcount, co.co_nlocals, co.co_stacksize,
        co.co_flags, codestr, tuple(newconsts), co.co_names,
        co.co_varnames, co.co_filename, co.co_name,
        co.co_firstlineno, co.co_lnotab, co.co_freevars,
        co.co_cellvars
        )

    return FunctionType(
        codeobj, function.func_globals, function.func_name,
        function.func_defaults, function.func_closure
        )

_make_constants = _make_constants(_make_constants) # optimise thyself!

def optimise_all(
    module_or_class, builtins=None, builtin_only=False, stoplist=(),
    constant_fold=True, verbose=False
    ):
    """Recursively apply constant binding to functions in a module or class."""

    try:
        d = vars(module_or_class)
    except TypeError:
        return

    for k, v in d.items():
        if type(v) is FunctionType:
            new_v = _make_constants(
                v, builtins, builtin_only, stoplist, constant_fold, verbose
                )
            setattr(module_or_class, k, new_v)
        elif type(v) in (type, ClassType):
            optimise_all(
                v, builtins, builtin_only, stoplist, constant_fold, verbose
                )

optimise_all = _make_constants(optimise_all)

@_make_constants
def optimise(
    builtins=None, builtin_only=False, stoplist=(), constant_fold=True,
    verbose=False
    ):
    """Return a decorator for optimising global references."""

    if type(builtins) is FunctionType:
        raise ValueError("The optimise decorator must have arguments.")

    return lambda f: _make_constants(
        f, builtins, builtin_only, stoplist, constant_fold, verbose
        )

def build_optimising_metaclass(
    builtins=None, builtin_only=False, stoplist=(), constant_fold=True,
    verbose=False
    ):
    """Return a automatically optimising metaclass for use as __metaclass__."""

    class _OptimisingMetaclass(type):
        def __init__(cls, name, bases, dict):
            super(_OptimisingMetaclass, cls).__init__(name, bases, dict)
            optimise_all(
                cls, builtins, builtin_only, stoplist, constant_fold, verbose
                )

    return _OptimisingMetaclass

OptimisingMetaclass = build_optimising_metaclass()
