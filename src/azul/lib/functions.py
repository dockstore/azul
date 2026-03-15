from functools import (
    reduce,
)
from typing import (
    Any,
    Callable,
)

from azul.lib.objects import (
    Sentinel,
    absent,
)


def compose[T, S, R](f: Callable[[S], R],
                     g: Callable[[T], S]
                     ) -> Callable[[T], R]:
    """
    >>> compose(hex, int)('42')
    '0x2a'
    """
    return lambda x: f(g(x))


def starcompose(f: Callable[[Any], Any],
                g: Callable[[Any], Any],
                *fs: Callable[[Any], Any]
                ) -> Callable[[Any], Any]:
    """
    >>> starcompose(hex, int)('42')
    '0x2a'

    >>> starcompose(str.upper, hex, int)('42')
    '0X2A'
    """
    if fs:
        return compose(f, compose(g, reduce(compose, fs)))
    else:
        return compose(f, g)


def iif[T, E](condition: bool, then: T, otherwise: E | Sentinel = absent) -> T | E:
    """
    An alternative to ``if`` expressions, that, in certain situations, might
    be more convenient or readable, such as when the ``else`` branch
    evaluates to the zero value of a given type. Example zero values are
    ``0`` for ``int``, ``[]`` for ``list``, ``()`` for ``tuple``, ``{}`` for
    ``dict`` and ``''`` for ``str``.

    Specifically, if the ``then`` and ``else`` branches of an ``if``
    expression yield values of the same type, and the ``else`` branch yields
    the zero value of that type, the ``if`` expression can be replaced with a
    call to ``iif`` that omits the 3rd argument. If the first argument in
    those calls evaluates to ``False``, ``iif`` returns a zero value, which
    is created by calling, without arguments, the constructor for the type of
    the 2nd argument.

    >>> iif(True, 42)
    42

    >>> iif(False, 42)
    0

    >>> iif(True, 42, None)
    42

    >>> iif(False, 42, None)

    >>> iif(False, [42])
    []

    Do not use ``iif`` as a replacement for an ``if`` expression whose
    branches are expensive to evaluate. ``if`` expressions are lazy, ``iif``
    is not:

    >>> 42 if True else 42/0
    42

    >>> iif(True, 42, 42/0)
    Traceback (most recent call last):
    ...
    ZeroDivisionError: division by zero
    """
    if condition:
        return then
    else:
        if absent.is_(otherwise):
            return type(then)()
        else:
            return otherwise


def either[T, E](value: T | None, alternative: E) -> T | E:
    return alternative if value is None else value
