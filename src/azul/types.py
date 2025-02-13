from collections.abc import (
    Mapping,
    Sequence,
)
from types import (
    UnionType,
    get_original_bases,
)
from typing import (
    Any,
    Callable,
    ForwardRef,
    Iterable,
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


def not_none[T](v: T | None) -> T:
    assert v is not None
    return v


PrimitiveJSON = str | int | float | bool | None

type AnyJSON = JSON | JSONArray | PrimitiveJSON
type JSON = Mapping[str, AnyJSON]
type JSONArray = Sequence[AnyJSON]
type JSONs = Sequence[JSON]
type CompositeJSON = JSON | JSONArray
type FlatJSON = Mapping[str, PrimitiveJSON]

# For mutable JSON we can be more specific and use dict and list:

type AnyMutableJSON = MutableJSON | MutableJSONArray | PrimitiveJSON
type MutableJSON = dict[str, AnyMutableJSON]
type MutableJSONArray = list[AnyMutableJSON]
type MutableJSONs = list[MutableJSON]
type MutableCompositeJSON = MutableJSON | MutableJSONArray
type MutableFlatJSON = dict[str, PrimitiveJSON]


def optional[A, R](f: Callable[[A], R], v: A) -> R | None:
    return v if v is None else f(v)


def json_mapping(v: AnyJSON) -> JSON:
    assert isinstance(v, Mapping), type(v)
    return v


def json_sequence(v: AnyJSON) -> JSONArray:
    assert isinstance(v, Sequence) and not isinstance(v, str), type(v)
    return v


def json_composite(v: AnyJSON) -> CompositeJSON:
    assert isinstance(v, (dict, list)), type(v)
    return v


def json_item_mappings(vs: AnyJSON) -> Iterable[tuple[str, JSON]]:
    for k, v in json_mapping(vs).items():
        yield k, json_mapping(v)


def json_element_mappings(vs: AnyJSON) -> Iterable[JSON]:
    return map(json_mapping, json_sequence(vs))


def json_dict(v: AnyMutableJSON) -> MutableJSON:
    assert isinstance(v, dict), type(v)
    return v


def json_list(v: AnyMutableJSON) -> MutableJSONArray:
    assert isinstance(v, list), type(v)
    return v


def json_item_dicts(vs: AnyMutableJSON) -> Iterable[tuple[str, MutableJSON]]:
    for k, v in json_dict(vs).items():
        yield k, json_dict(v)


def json_element_dicts(vs: AnyMutableJSON) -> Iterable[MutableJSON]:
    return map(json_dict, json_list(vs))


def json_str(v: AnyMutableJSON | AnyJSON) -> str:
    assert isinstance(v, str), type(v)
    return v


def json_int(v: AnyMutableJSON | AnyJSON) -> int:
    assert isinstance(v, int), type(v)
    return v


def json_float(v: AnyMutableJSON | AnyJSON) -> float:
    assert isinstance(v, float), type(v)
    return v


def json_bool(v: AnyMutableJSON | AnyJSON) -> bool:
    assert isinstance(v, bool), type(v)
    return v


def json_none(v: AnyMutableJSON | AnyJSON) -> None:
    assert v is None, type(v)
    return v


class LambdaContext:
    """
    A stub for the AWS Lambda context
    """

    aws_request_id: str

    log_group_name: str

    log_stream_name: str

    function_name: str

    memory_limit_in_mb: str

    function_version: str

    invoked_function_arn: str

    def get_remaining_time_in_millis(self) -> int: ...  # type: ignore[empty-body]

    def log(self, msg: str) -> None: ...


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


def derived_type_params(cls: type,
                        root: type | None = None,
                        ) -> tuple[type | TypeVar | ForwardRef, ...]:
    """
    Inspect the type parameterization of a generic class, or a class derived
    from a generic class.

    Each of the returned values is either an instance of ``type``, of
    ``typing.TypeVar``, or of ``typing.ForwardRef``, depending on how the
    parameter is written in the definition of the root class.

    Caveat: This function was only tested with classes that use the syntax from
    PEP-695 to define type parameters. Every class in the ancestry of the given
    class must use the new syntax. Note that PEP-695 introduced semantic changes
    as well, mostly with respect to scoping and variance.

    Caveat: This function was not tested with multiple inheritance. It should
    generally, work but diamond-shaped ancestry may be problematic.

    :param cls: The class to be inspected

    :param root: The upper bound up to which the ancestry of ``cls`` is
                 inspected. If this argument is ``None``, only the first parent
                 of ``cls`` is inspected. Otherwise, the ancestry of ``cls`` is
                 searched for ``root``. The search is "height-first", as in
                 depth-first but going upwards. If the root is found in the
                 ancestry, the parameterization of every ancestor on the lineage
                 from ``cls`` to ``root`` is then inspected.

    A generic class:

    >>> class A[T1, T2]: pass

    >>> derived_type_params(A)
    (T1, T2)

    Both T1 and T2 are instances of ``TypeVar``.

    A non-generic subclass:

    >>> class B(A[int, float]): pass

    >>> derived_type_params(B)
    (<class 'int'>, <class 'float'>)

    A non-generic subclass, using a forward reference. The reference is
    returned:

    >>> class C(A['foo', int]): pass

    >>> derived_type_params(C)
    (ForwardRef('foo'), <class 'int'>)

    A generic class that binds the first of the parent's parameters, but leaves
    the second one open:

    >>> class D[T](A[int, T]): pass

    >>> derived_type_params(D)
    (<class 'int'>, T)

    A non-generic subclass that binds the remaining parameter as well:

    >>> class E(D[float]): pass

    The value that E bind's D's parameter to:

    >>> derived_type_params(E)
    (<class 'float'>,)

    The equivalent invocation explicitly specifying the first parent class:

    >>> derived_type_params(E, root=D)
    (<class 'float'>,)

    E does not inherit B, so an exception is raised:

    >>> derived_type_params(E, root=B)
    Traceback (most recent call last):
    ...
    TypeError: ('Root is not an ancestor', <class 'azul.types.B'>, <class 'azul.types.E'>)

    Last but not least, the most useful invocation: specifying the oldest
    generic ancestor as the root. This invocation returns the parameterization
    of E's grandparent.

    >>> derived_type_params(E, root=A)
    (<class 'int'>, <class 'float'>)

    Same as above but through a parent that binds the second of the
    grandparent's parameters:

    >>> class F[T](A[T, int]): pass
    >>> class G(F[float]): pass
    >>> derived_type_params(G, root=A)
    (<class 'float'>, <class 'int'>)

    A parent swapping the grandparent's type parameters:

    >>> class H[T1, T2](A[T2, T1]): pass
    >>> class J(H[int, float]): pass
    >>> derived_type_params(J, root=A)
    (<class 'float'>, <class 'int'>)

    >>> t1, t2 = derived_type_params(A)
    >>> derived_type_params(t1)
    Traceback (most recent call last):
    ...
    AssertionError: R('Not a type', T1, <class 'typing.TypeVar'>)
    """
    from azul import (
        R,
    )
    assert isinstance(cls, type), R('Not a type', cls, type(cls))

    def ancestors(cls) -> tuple[type, ...]:
        for base in get_original_bases(cls):
            origin = get_origin(base)
            if origin is None:
                return ()
            elif origin is root:
                return (base,)
            else:
                lineage = ancestors(origin)
                if lineage:
                    return base, *lineage
        return ()

    if root is None:
        base = get_original_bases(cls)[0]
        return get_args(base)
    else:
        bases = iter(ancestors(cls))
        if None is (base := next(bases, None)):
            raise TypeError('Root is not an ancestor', root, cls)
        else:
            mapping = None
            while True:
                values = get_args(base)
                if mapping:
                    values = tuple(mapping.get(value, value) for value in values)
                if None is (next_base := next(bases, None)):
                    return values
                else:
                    origin = get_origin(base)
                    assert origin is not None, (base, type(base))
                    params = origin.__type_params__
                    mapping = {param: value for param, value in zip(params, values)}
                    base = next_base


class SupportsLessAndGreaterThan(Protocol):

    def __lt__(self, __other: Any) -> bool: ...

    def __gt__(self, __other: Any) -> bool: ...
