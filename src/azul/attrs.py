from abc import (
    ABCMeta,
    abstractmethod,
)
from itertools import (
    count,
)
from types import (
    UnionType,
)
from typing import (
    Any,
    Callable,
    Iterator,
    Literal,
    Optional,
    Self,
    Tuple,
    TypeVar,
    TypedDict,
    Union,
    final,
    get_args,
    get_origin,
)
from uuid import (
    UUID,
)

import attrs
from more_itertools import (
    flatten,
    one,
)

from azul import (
    R,
    require,
)
from azul.json import (
    Serializable,
)
from azul.types import (
    AnyJSON,
    CompositeJSON,
    JSON,
    JSONArray,
    MutableCompositeJSON,
    MutableJSON,
    MutableJSONArray,
    PrimitiveJSON,
    derived_type_params,
    json_mapping,
    not_none,
    reify,
)


def strict_auto(*args, **kwargs):
    """
    A field that uses the annotated type for validation.

    See :func:`as_annotated` for details
    """
    return attrs.field(*args, validator=as_annotated(), **kwargs)


def as_annotated():
    """
    Returns a validator that verifies that a field's value is of the annotated
    type. Has some limited magic for parameterized types such as typing.Union
    and typing.Optional.

    >>> from azul.types import AnyJSON
    >>> @attrs.define
    ... class Foo:
    ...     x: Optional[bool] = strict_auto()
    ...     y: AnyJSON = strict_auto()

    >>> Foo(x=None, y={}), Foo(x=True, y=[]), Foo(x=False, y='foo')
    (Foo(x=None, y={}), Foo(x=True, y=[]), Foo(x=False, y='foo'))

    >>> # noinspection PyTypeChecker
    >>> Foo(x='foo', y={})
    Traceback (most recent call last):
    ...
    TypeError: ('x', 'foo', (<class 'bool'>, <class 'NoneType'>))

    >>> # noinspection PyTypeChecker
    >>> Foo(x=None, y=set())
    ... # doctest: +NORMALIZE_WHITESPACE
    Traceback (most recent call last):
    ...
    TypeError: ('y', set(), (<class 'collections.abc.Mapping'>,
    <class 'collections.abc.Sequence'>, <class 'str'>, <class 'int'>,
    <class 'float'>, <class 'bool'>, <class 'NoneType'>))

    Note that you cannot share one return value of this function between more
    than one field.

    >>> validator = as_annotated()
    >>> @attrs.define
    ... class Bar:
    ...     x: int = attrs.field(validator=validator)
    ...     y: str = attrs.field(validator=validator)
    >>> Bar(x=1, y='')
    ... # doctest: +NORMALIZE_WHITESPACE
    Traceback (most recent call last):
        ...
    azul.RequirementError: ('Validator cannot be shared among fields',
    Attribute(name='x', default=NOTHING, validator=as_annotated(), repr=True,
    eq=True, eq_key=None, order=True, order_key=None, hash=None, init=True,
    metadata=mappingproxy({}), type=<class 'int'>, converter=None,
    kw_only=False, inherited=False, on_setattr=None, alias='x'),
    Attribute(name='y', default=NOTHING, validator=as_annotated(), repr=True,
    eq=True, eq_key=None, order=True, order_key=None, hash=None, init=True,
    metadata=mappingproxy({}), type=<class 'str'>, converter=None,
    kw_only=False, inherited=False, on_setattr=None, alias='y'))

    Unfortunately, this sharing violation is currently detected very late,
    during the first instantiation of a class that reuses a validator.

    >>> validator = as_annotated()
    >>> @attrs.define
    ... class Bar:
    ...     x: int = attrs.field(validator=validator)
    >>> @attrs.define
    ... class Foo:
    ...     y: str = attrs.field(validator=validator)
    >>> Bar(x=1)
    Bar(x=1)
    >>> Foo(y='')
    ... # doctest: +ELLIPSIS
    Traceback (most recent call last):
        ...
    azul.RequirementError: ('Validator cannot be shared among fields', ...

    """
    return _AsAnnotated()


class _AsAnnotated:
    _cache: Optional[Tuple[attrs.Attribute, Union[type, Tuple[type]]]] = None

    def __call__(self, _instance, field, value):
        reified_type = self._reify(field)
        if not isinstance(value, reified_type):
            raise TypeError(field.name, value, reified_type)

    def _reify(self, field):
        # reify() isn't exactly cheap so we'll cache its result
        if self._cache is None:
            reified_types = reify(field.type)
            self._cache = field, reified_types
        else:
            cached_field, reified_types = self._cache
            require(cached_field == field,
                    'Validator cannot be shared among fields', cached_field, field)
        return reified_types

    def __repr__(self):
        return 'as_annotated()'


def is_uuid(version):
    def validator(_instance, field, value):
        if not isinstance(value, UUID) or value.version != version:
            raise TypeError(f'Not a UUID{version}', field.name, value)

    return validator


type Source = list[str | tuple[str, ...] | Source]


class SerializableAttrs(Serializable, attrs.AttrsInstance):
    """
    >>> @attrs.frozen(kw_only=True)
    ... class InnerBase(SerializableAttrs):
    ...     x: int

    >>> @attrs.frozen(kw_only=True)
    ... class MiddleInner[T](InnerBase):
    ...     y: T | None

    >>> @attrs.frozen(kw_only=True)
    ... class Inner(MiddleInner[str]): ...

    >>> @attrs.frozen(kw_only=True)
    ... class OuterBase[X, T: InnerBase](SerializableAttrs):
    ...     inner: list[T] | None

    >>> class MiddleOuter[X](OuterBase[X, Inner]): ...

    >>> class Outer(MiddleOuter[float]): ...

    >>> outer = Outer(inner=[Inner(x=1, y='b')])
    >>> outer.to_json()
    {'inner': [{'x': 1, 'y': 'b'}]}

    >>> Outer.from_json(outer.to_json())
    Outer(inner=[Inner(x=1, y='b')])

    >>> Outer(inner=None).to_json()
    {'inner': None}

    >>> Outer.from_json({'inner': None})
    Outer(inner=None)

    >>> Outer.from_json({'inner': [{'x': 'bad', 'y': 'b'}]})
    Traceback (most recent call last):
    ...
    ValueError: ('Invalid type of value', <class 'str'>, 'expecting', <class 'int'>)

    >>> Outer.from_json({'inner': [{'x': 1, 'y': None}]})
    Outer(inner=[Inner(x=1, y=None)])

    A class with custom serialization (float serialized as string):

    >>> @attrs.frozen(kw_only=True)
    ... class CustomBase(SerializableAttrs):
    ...     x: float
    ...
    ...     def to_json(self) -> JSON:
    ...         return super().to_json() | {'x': str(self.x)}
    ...
    ...     @classmethod
    ...     def _from_json(cls, json: JSON) -> dict[str, Any]:
    ...         return dict(super()._from_json(json), x=float(json['x']))

    >>> @attrs.frozen(kw_only=True)
    ... class Custom(CustomBase):
    ...     y: str

    >>> Custom(x=1.23, y='y').to_json()
    {'x': '1.23', 'y': 'y'}

    >>> Custom.from_json({'x': '1.23', 'y': 'y'})
    Custom(x=1.23, y='y')

    >>> @attrs.frozen(kw_only=True)
    ... class Embedded(SerializableAttrs):
    ...     x: JSON

    >>> Embedded(x={'y': 12}).to_json()
    {'x': {'y': 12}}

    >>> @attrs.frozen(kw_only=True)
    ... class WithDicts(SerializableAttrs):
    ...     inners: dict[int, Inner]

    >>> WithDicts(inners={1: Inner(x=1, y='b')}).to_json()
    {'inners': {1: {'x': 1, 'y': 'b'}}}

    >>> WithDicts.from_json({'inners': {1: {'x': 1, 'y': 'b'}}})
    WithDicts(inners={1: Inner(x=1, y='b')})
    """

    @classmethod
    @final
    def from_json(cls, json: AnyJSON) -> Self:
        assert not cls._deferred_fields, R(
            'Class has fields of unknown type', cls._deferred_fields)
        kwargs = cls._from_json(json_mapping(json))
        return cls(**kwargs)

    @classmethod
    def _from_json(cls, json: JSON) -> dict[str, Any]:
        """
        Return a dictionary with keyword arguments for the constructor. An
        override must call the overridden method via super() but only need to
        populate keyword arguments for the fields defined by the class that
        overrides the method. Typically, the overrides in subclasses will be
        generated automatically but if a subclass explicitly defines an
        override, it will be left alone.
        """
        return {}

    def to_json(self) -> dict[str, AnyJSON]:
        """
        Typically, the overrides in subclasses will be generated automatically
        but if a subclass explicitly defines an override, it will be left alone.
        """
        return {}

    class Metadata(TypedDict):
        from_json: Callable[[AnyJSON], Any] | None
        to_json: Callable[[Any], AnyJSON] | None

    def __init_subclass__(cls):
        super().__init_subclass__()
        try:
            fields = attrs.fields(cls)
        except attrs.exceptions.NotAnAttrsClassError:
            pass
        else:
            cls._instrument(fields)

    @classmethod
    def __attrs_init_subclass__(cls):
        cls._instrument(attrs.fields(cls))

    #: The names of fields that we weren't able to generate code for in this
    #: class because at least one of them was annotated with a variable type.
    #: Generic descendants that use free type variables in their attrs field
    #: annotations override this attribute to a non-empty set. The
    #: responsibility to handle deferred fields falls on the descendant that
    #: binds the last remaining free type variable.
    #:
    _deferred_fields: frozenset[str] = frozenset()

    @classmethod
    def _instrument(cls, fields: list[attrs.Attribute]):
        """
        Add overrides for to_json and _from_json to the given class. The
        overrides will handle the serialization and deserialization of the
        fields defined by the class, not those that it inherits. An override
        will only be added if the class doesn't already provide one. This method
        must be idempotent because it may be invoked twice for the same class,
        before and after the attrs decorator did its work. Even for slotted
        classes this method will be invoked twice, albeit the second time on a
        copy of the class.
        """
        # When slots=True (the default for attrs.define), attrs makes a copy of
        # the class so the subclass hook will be invoked twice, once for the
        # original class, and again for the copy. The copy is likely to have
        # additional fields defined so we need to start from scratch and reset
        # any left-overs that would interfere with that.
        #
        if cls._has_custom('to_json') and cls._has_custom('_from_json'):
            pass
        else:
            if '_deferred_fields' in cls.__dict__:
                del cls._deferred_fields
            owned_fields = [
                field
                for field in fields
                if field.name in cls.__annotations__ or field.name in cls._deferred_fields
            ]
            if owned_fields:
                deferred_fields = cls._make(owned_fields)
                if deferred_fields != cls._deferred_fields:
                    cls._deferred_fields = deferred_fields

    @classmethod
    def _make(cls, fields: list[attrs.Attribute]) -> frozenset[str]:
        try:
            _from_json = cls._make_from_json(fields)
        except cls.Strategy.MustDefer:
            deferred_fields = frozenset(field.name for field in fields)
        else:
            cls._define(_from_json)
            deferred_fields = frozenset()
            to_json = cls._make_to_json(fields)
            cls._define(to_json)
        return deferred_fields

    @classmethod
    def _serializable(cls,
                      field: attrs.Attribute,
                      key: Literal['from_json', 'to_json']
                      ) -> bool:
        try:
            return field.metadata['azul'][key] is not None
        except KeyError:
            return True

    @classmethod
    def _make_from_json(cls, fields: list[attrs.Attribute]) -> Callable:
        globals = {cls.__name__: cls}
        source = cls._indent([
            '@classmethod',
            'def _from_json(cls, json):', [
                f'kwargs = super({cls.__name__}, cls)._from_json(json)',
                *flatten(
                    [
                        f'x = json["{field.name}"]',
                        *(cls.Deserializer(cls, field, globals).handle('x')),
                        f'kwargs["{field.name}"] = x'
                    ]
                    for field in fields
                    if cls._serializable(field, 'from_json')
                ),
                'return kwargs'
            ]
        ])
        return cls._compile(source, globals)

    @classmethod
    def _make_to_json(cls, fields: list[attrs.Attribute]) -> Callable:
        globals = {cls.__name__: cls}
        to_json = cls._indent([
            'def to_json(self):', [
                # Using the super() shortcut would require messing with the
                # ``__closure__`` attribute of the function, and, we assume,
                # would be slower.
                f'json = super({cls.__name__}, self).to_json()',
                *flatten(
                    [
                        f'x = self.{field.name}',
                        f'json["{field.name}"] = ' + cls.Serializer(cls, field, globals).handle('x')
                    ]
                    for field in fields
                    if cls._serializable(field, 'to_json')
                ),
                'return json'
            ]
        ])
        return cls._compile(to_json, globals)

    @classmethod
    def _indent(cls, source: Source, level=0):
        """
        Indent and join the given list of source code items. An item can be
        either a line, a tuple of words, or a nested list of items. The
        indentation of lines is based on the nesting of the lists. Lines are
        joined with a newline character, words are joined with a comma.
        """
        return '\n'.join(
            cls._indent(v, level + 1)
            if isinstance(v, list) else
            ' ' * level * 4 + (', '.join(v) if isinstance(v, tuple) else v)
            for v in source
        )

    @classmethod
    def _compile(cls, source: str, globals: dict[str, Any]):
        """
        Compile a function definition from the given source & context
        """
        bytecode = compile(source, cls.__module__, 'exec')
        locals: dict[str, Any] = {}
        eval(bytecode, globals, locals)
        function = one(locals.values())
        return function

    _method_marker = '__azul_serializable__'

    @classmethod
    def _has_custom(cls, method_name):
        method = cls.__dict__.get(method_name)
        return method is not None and not hasattr(method, cls._method_marker)

    @classmethod
    def _define(cls, function: Callable) -> None:
        """
        Add the given function as a method of the class to be instrumented
        """
        method_name = function.__name__
        custom = cls._has_custom(method_name)
        # We should never replace a custom definition. However, an
        # instrumentation during attrs' subclass hook must replace
        # the definition from the standard subclass hook.
        if not custom:
            setattr(function, cls._method_marker, None)
            setattr(cls, method_name, function)

    @attrs.frozen
    class Strategy[T](metaclass=ABCMeta):
        cls: type['SerializableAttrs']
        field: attrs.Attribute
        globals: dict[str, Any]
        depth: Iterator[int] = attrs.field(factory=count)

        class MustDefer(Exception):
            pass

        def handle(self, x: str) -> T:
            try:
                metadata = self.field.metadata['azul']
            except KeyError:
                return self._handle(x, self._reify(self.field.type))
            else:
                return self._custom(x, metadata)

        def _owner(self) -> type:
            """
            Find the nearest ancestor that introduced the given field
            """
            for base in self.cls.__mro__:
                if self.field.name in base.__annotations__:
                    assert isinstance(base, type)
                    assert issubclass(base, SerializableAttrs)
                    return base
            assert False

        def _reify(self, field_type: Any) -> Any:
            """
            Resolve the type parameters of the given type, or raise
            MustDefer if that's not possible.
            """
            while isinstance(field_type, TypeVar):
                owner = self._owner()
                if owner is self.cls:
                    raise self.MustDefer
                params = derived_type_params(self.cls, root=owner)
                try:
                    field_type = params[field_type]
                except KeyError:
                    raise self.MustDefer
            return field_type

        def _handle(self, x: str, field_type: Any):
            if isinstance(field_type, type):
                if field_type in reify(PrimitiveJSON):
                    return self._primitive(x, field_type)
                elif issubclass(field_type, Serializable):
                    inner_cls_name = field_type.__name__
                    self.globals[inner_cls_name] = field_type
                    return self._serializable(x, inner_cls_name)
            else:
                if field_type in (JSON, CompositeJSON, JSONArray, MutableJSON, MutableCompositeJSON, MutableJSONArray):
                    return self._embedded_json(x, one(reify(field_type)))
                else:
                    origin = get_origin(field_type)
                    if origin in (Union, UnionType):
                        arg_types = set(get_args(field_type))
                        arg_types.discard(type(None))
                        if len(arg_types) == 1:
                            field_type = self._reify(one(arg_types))
                            return self._optional(x, field_type)
                    elif issubclass(origin, list):
                        item_type = one(get_args(field_type))
                        item_type = self._reify(item_type)
                        return self._list(x, item_type)
                    elif issubclass(origin, dict):
                        key_type, value_type = map(self._reify, get_args(field_type))
                        return self._dict(x, key_type, value_type)
            raise TypeError('Unserializable field', field_type, self.field)

        @abstractmethod
        def _primitive(self, x: str, field_type: type) -> T:
            raise NotImplementedError

        @abstractmethod
        def _embedded_json(self, x: str, field_type: type) -> T:
            raise NotImplementedError

        @abstractmethod
        def _optional(self, x: str, field_type: type) -> T:
            raise NotImplementedError

        @abstractmethod
        def _serializable(self, x: str, inner_cls_name: str) -> T:
            raise NotImplementedError

        @abstractmethod
        def _list(self, x: str, item_type: type) -> T:
            raise NotImplementedError

        @abstractmethod
        def _dict(self, x: str, key_type: type, value_type: type) -> T:
            raise NotImplementedError

        @abstractmethod
        def _custom(self, x: str, metadata: 'SerializableAttrs.Metadata') -> T:
            raise NotImplementedError

    class Deserializer(Strategy[Source]):

        def _optional(self, x: str, field_type: type) -> Source:
            return [
                f'if {x} is not None:', self._handle(x, field_type)
            ]

        def _serializable(self, x: str, inner_cls_name: str) -> Source:
            return [
                f'{x} = {inner_cls_name}.from_json({x})'
            ]

        def _primitive(self, x: str, field_type: type) -> Source:
            return [
                f'if not isinstance({x}, {field_type.__name__}):', [
                    'raise ValueError(', [(
                        '"Invalid type of value"',
                        f'type({x})',
                        '"expecting"',
                        field_type.__name__,
                    )], ')'
                ]
            ]

        def _embedded_json(self, x: str, field_type: type) -> Source:
            self.globals[field_type.__name__] = field_type
            return self._primitive(x, field_type)

        def _list(self, x: str, item_type: type) -> Source:
            depth = next(self.depth)
            l, v = f'l{depth}', f'v{depth}'
            return [
                f'{l} = []',
                f'for {v} in {x}:', [
                    *self._handle(v, item_type),
                    f'{l}.append({v})'
                ],
                f'{x} = {l}'
            ]

        def _dict(self, x: str, key_type: type, value_type: type) -> Source:
            level = next(self.depth)
            d, k, v = f'd{level}', f'k{level}', f'v{level}'
            return [
                f'{d} = {{}}',
                f'for {k},{v} in {x}.items():', [
                    *self._handle(k, key_type),
                    *self._handle(v, value_type),
                    f'{d}[{k}] = {v}'
                ],
                f'{x} = {d}'
            ]

        def _custom(self, x: str, metadata: 'SerializableAttrs.Metadata') -> Source:
            var_name = self.field.name + '_from_json'
            self.globals[var_name] = not_none(metadata['from_json'])
            return [
                f'{x} = {var_name}({x})'
            ]

    class Serializer(Strategy[str]):

        def _primitive(self, x: str, field_type: type) -> str:
            return x

        def _embedded_json(self, x: str, field_type: type) -> str:
            return x

        def _optional(self, x: str, field_type: type) -> str:
            return f'{x} if {x} is None else ({self._handle(x, field_type)})'

        def _serializable(self, x: str, inner_cls_name: str) -> str:
            return f'{x}.to_json()'

        def _list(self, x: str, item_type: type) -> str:
            depth = next(self.depth)
            v = f'v{depth}'
            v_ = self._handle(v, item_type)
            return f'[({v_}) for {v} in {x}]'

        def _dict(self, x: str, key_type: type, value_type: type) -> str:
            level = next(self.depth)
            k, v = f'k{level}', f'v{level}'
            k_, v_ = self._handle(k, key_type), self._handle(v, value_type)
            return f'{{{k_}: {v_} for {k}, {v} in x.items()}}'

        def _custom(self, x: str, metadata: 'SerializableAttrs.Metadata') -> str:
            var_name = self.field.name + '_to_json'
            self.globals[var_name] = not_none(metadata['to_json'])
            return f'{var_name}({x})'


def serializable[T: attrs.Attribute](field: T,
                                     from_json: Callable[[AnyJSON], Any],
                                     to_json: Callable[[Any], AnyJSON]) -> T:
    """
    Use the provided callables to (de)serialize values of the given field,
    instead of generating them.

    >>> @attrs.frozen
    ... class Foo(SerializableAttrs):
    ...     x: set[str] = serializable(attrs.field(), to_json=sorted, from_json=set)

    >>> Foo(x={'b','a'}).to_json()
    {'x': ['a', 'b']}

    >>> Foo.from_json({'x': ['a']})
    Foo(x={'a'})
    """
    field.metadata['azul'] = SerializableAttrs.Metadata(from_json=from_json,
                                                        to_json=to_json)
    return field


def not_serializable[T: attrs.Attribute](field: T) -> T:
    """
    Skip the given field during (de)serialization. The field should have a
    default value or there should be some other provision for the constructor to
    handle the case that no argument will be passed to it for any field that was
    marked this way.

    >>> @attrs.frozen
    ... class Foo(SerializableAttrs):
    ...     x: int = not_serializable(attrs.field(default=42))

    >>> Foo().to_json()
    {}

    >>> Foo.from_json({})
    Foo(x=42)
    """
    field.metadata['azul'] = SerializableAttrs.Metadata(from_json=None,
                                                        to_json=None)
    return field
