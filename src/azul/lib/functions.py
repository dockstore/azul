from functools import (
    reduce,
)
from typing import (
    Any,
    Callable,
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
