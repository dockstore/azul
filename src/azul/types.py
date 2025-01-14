from collections.abc import (
    Mapping,
    Sequence,
)
from types import (
    UnionType,
)
from typing import (
    Any,
    ForwardRef,
    Optional,
    Protocol,
    TypeAliasType,
    TypeVar,
    Union,
    get_args,
    get_origin,
)

from azul.collections import (
    OrderedSet,
)

PrimitiveJSON = str | int | float | bool | None

# Not every instance of Mapping or Sequence can be fed to json.dump() but those
# two generic types are the most specific *immutable* super-types of `list`,
# `tuple` and `dict`:

type AnyJSON = Sequence[AnyJSON] | Mapping[str, AnyJSON] | PrimitiveJSON
type JSON = Mapping[str, AnyJSON]
type JSONs = Sequence[JSON]
type CompositeJSON = JSON | Sequence[AnyJSON]
type FlatJSON = Mapping[str, PrimitiveJSON]

# For mutable JSON we can be more specific and use dict and list:

type AnyMutableJSON = list[AnyMutableJSON] | dict[str, AnyMutableJSON] | PrimitiveJSON
type MutableJSON = dict[str, AnyMutableJSON]
type MutableJSONs = list[MutableJSON]
type MutableCompositeJSON = MutableJSON | list[AnyJSON]
type MutableFlatJSON = dict[str, PrimitiveJSON]


class LambdaContext(object):
    """
    A stub for the AWS Lambda context
    """

    @property
    def aws_request_id(self) -> str:
        raise NotImplementedError

    @property
    def log_group_name(self) -> str:
        raise NotImplementedError

    @property
    def log_stream_name(self) -> str:
        raise NotImplementedError

    @property
    def function_name(self) -> str:
        raise NotImplementedError

    @property
    def memory_limit_in_mb(self) -> str:
        raise NotImplementedError

    @property
    def function_version(self) -> str:
        raise NotImplementedError

    @property
    def invoked_function_arn(self) -> str:
        raise NotImplementedError

    def get_remaining_time_in_millis(self) -> int:
        raise NotImplementedError

    def log(self, msg: str) -> None:
        raise NotImplementedError


def is_optional(t) -> bool:
    """
    :param t: A type or type annotation.

    :return: True if theargument is equivalent to typing.Optional

    https://stackoverflow.com/a/62641842/4171119

    >>> is_optional(str)
    False

    >>> is_optional(Optional[str])
    True

    >>> is_optional(Union[str, None])
    True
    >>> is_optional(Union[None, str])
    True
    >>> is_optional(Union[str, None, int])
    True
    >>> is_optional(Union[str, int])
    False

    >>> is_optional(str | None)
    True
    >>> is_optional(None | str)
    True
    >>> is_optional(str | None | int)
    True
    >>> is_optional(str | int)
    False
    """
    return t == Optional[t]


def reify(t):
    """
    Given a parameterized type construct, return a tuple of subclasses of
    ``type`` representing all possible alternatives that can pass for that
    construct at runtime. The return value is meant to be used as the second
    argument to the ``isinstance`` or ``issubclass`` built-ins.

    >>> reify(int)
    (<class 'int'>,)

    >>> reify(Union[int])
    (<class 'int'>,)

    >>> reify(str | int)
    (<class 'str'>, <class 'int'>)

    >>> reify(Union[str, int])
    (<class 'str'>, <class 'int'>)

    >>> reify(str | Union[int, set])
    (<class 'str'>, <class 'int'>, <class 'set'>)

    >>> reify(Union[str | int, set])
    (<class 'str'>, <class 'int'>, <class 'set'>)

    >>> isinstance({}, reify(AnyJSON))
    True

    >>> isinstance({}, reify(JSON))
    True

    >>> isinstance([], reify(JSON))
    False

    >>> isinstance([], reify(JSONs))
    True

    >>> from collections import Counter
    >>> issubclass(Counter, reify(AnyJSON))
    True

    >>> isinstance([], reify(AnyJSON))
    True

    >>> isinstance((), reify(AnyJSON))
    True

    >>> isinstance(42, reify(AnyJSON))
    True

    >>> isinstance(set(), reify(AnyJSON))
    False

    >>> set(reify(Optional[int])) == {type(None), int}
    True

    >>> reify(TypeVar)
    Traceback (most recent call last):
        ...
    ValueError: ('Not a reifiable generic type', <class 'typing.TypeVar'>)

    >>> reify(Union)
    Traceback (most recent call last):
        ...
    ValueError: ('Not a reifiable generic type', typing.Union)
    """

    def reify(t):
        while isinstance(t, TypeAliasType):
            t = t.__value__
        o = get_origin(t)
        # While `int | str` constructs a `UnionType` instance, `Union[str, int]`
        # constructs an instance of `Union`, so we need to handle both.
        if o in (UnionType, Union):
            for a in get_args(t):
                yield from reify(a)
        elif o is not None:
            yield o
        elif t.__module__ == 'typing':
            raise ValueError('Not a reifiable generic type', t)
        else:
            yield t

    return tuple(OrderedSet(reify(t)))


def get_generic_type_params(cls: type,
                            *required_types: type
                            ) -> tuple[type | TypeVar | ForwardRef, ...]:
    """
    Inspect and validate the type parameters of a subclass of `typing.Generic`.

    The type of each returned parameter may be a type, a `typing.TypeVar`, or a
    `typing.ForwardRef`, depending on how the parameter is written in the
    inspected class's definition. `*required_types` can be used to assert the
    superclasses of parameters that are types.

    >>> class A[T]:
    ...     pass
    >>> class B(A[int]):
    ...     pass
    >>> class C(A['foo']):
    ...     pass

    >>> get_generic_type_params(A)
    (T,)

    >>> get_generic_type_params(A, str)
    (T,)

    >>> get_generic_type_params(B)
    (<class 'int'>,)

    >>> get_generic_type_params(B, str)
    Traceback (most recent call last):
    ...
    AssertionError: (<class 'int'>, <class 'str'>)

    >>> get_generic_type_params(B, int, int)
    Traceback (most recent call last):
    ...
    AssertionError: 1

    >>> get_generic_type_params(C)
    (ForwardRef('foo'),)
    """
    base_cls = getattr(cls, '__orig_bases__')[0]
    types = get_args(base_cls)
    if required_types:
        assert len(required_types) == len(types), len(types)
        for required_type, type_ in zip(required_types, types):
            if isinstance(type_, type):
                assert issubclass(type_, required_type), (type_, required_type)
            else:
                assert isinstance(type_, (TypeVar, ForwardRef)), type_
    return types


class SupportsLessAndGreaterThan(Protocol):

    def __lt__(self, __other: Any) -> bool: ...

    def __gt__(self, __other: Any) -> bool: ...
