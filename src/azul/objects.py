from azul import (
    lru_cache,
)


class InternMeta(type):
    """
    A metaclass that interns instances of its instances such that the invariant
    (x == y) == (x is y) holds for all instances x and y of any instance of this
    metaclass. Note that an instance of a metaclass is a class.

    This metaclass does not consider thread safety. It should be as safe or
    unsafe as lru_cache from functools.

    Note also that this metaclass never releases the memory used by instances of
    its instances.

    >>> from typing import ClassVar

    >>> class C(metaclass=InternMeta):
    ...     i: ClassVar[int] = 0
    ...     def __init__(self, x:int):
    ...         C.i += 1
    ...         self.x = x

    >>> C(1) is C(1)
    True
    >>> C.i
    1
    >>> C(1) is C(2)
    False
    >>> C.i
    2

    Instances of an instance of this metaclass should be immutable.

    >>> from dataclasses import dataclass, field
    >>> @dataclass
    ... class D(metaclass=InternMeta):
    ...     x: int
    >>> d1, d2 = D(1), D(2)
    >>> d1 == d2
    False
    >>> d2.x = 1  # make them equal
    >>> d1 == d2
    True
    >>> d1 is d2  # but they are still not the same, violating the invariant.
    False

    Instances of an instance are interned based on the arguments they were
    constructed with. That means that instance equality must be consistent with
    the equality of the construction arguments. If it isn't i.e., if two
    instances are equal even if their construction arguments are not, the
    invariant will be violated.

    >>> @dataclass
    ... class E(metaclass=InternMeta):
    ...     x: int
    ...     y: int = field(compare=False)
    >>> e1, e2 = E(1, 1), E(1, 2)
    >>> e1.y == e2.y  # Even though .y is differs between instances …
    False
    >>> e1 == e2  # they are considered equal because .y is insignificant for equality.
    True
    >>> e1 is e2  # Invariant is invalidated.
    False
    """

    @lru_cache
    def __call__(cls, *args, **kwargs):
        return super().__call__(*args, **kwargs)
