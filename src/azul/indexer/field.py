from abc import (
    ABCMeta,
    abstractmethod,
)
from datetime import (
    datetime,
    timezone,
)
import sys
from typing import (
    Generic,
    Mapping,
    Sequence,
    TypeVar,
    get_args,
)

from more_itertools import (
    first,
    one,
)

from azul import (
    CatalogName,
    JSON,
)
from azul.openapi import (
    schema,
)
from azul.time import (
    format_dcp2_datetime,
    parse_dcp2_datetime,
)
from azul.types import (
    AnyJSON,
    PrimitiveJSON,
    reify,
)

# The native type of the field in documents as they are being created by a
# transformer or processed by an aggregator.
N = TypeVar('N')

# The type of the field in a document just before it's being written to the
# index. Think "translated type".
T = TypeVar('T', bound=AnyJSON)

P = TypeVar('P', bound=PrimitiveJSON)

Range = tuple[P, P]


class FieldType(Generic[N, T], metaclass=ABCMeta):
    shadowed: bool = False
    es_sort_mode: str = 'min'
    allow_sorting_by_empty_lists: bool = True

    def __init__(self, native_type: type[N], translated_type: type[T]):
        self.native_type = native_type
        self.translated_type = translated_type

    @property
    @abstractmethod
    def es_type(self) -> str | None:
        raise NotImplementedError

    @abstractmethod
    def to_index(self, value: N) -> T:
        raise NotImplementedError

    @abstractmethod
    def from_index(self, value: T) -> N:
        raise NotImplementedError

    def to_tsv(self, value: N) -> str:
        return '' if value is None else str(value)

    @property
    def api_schema(self) -> JSON:
        """
        The JSONSchema describing fields of this type in OpenAPI specifications.
        """
        return schema.schema(self.native_type)

    def from_api(self, value: AnyJSON) -> N:
        """
        Convert a deserialized JSON value occurring as an input to a REST API
        to the native representation of values of this field type.

        The default implementation assumes that the REST API representation
        of the value is already of the native type, and just returns the
        argument. Subclasses must override this if the native and API
        representations differ. An API representation of a field only occurs
        in inputs to a REST API. Outputs like the body of a response use the
        native representation.
        """
        assert isinstance(value, reify(self.native_type))
        return value

    @property
    def supported_filter_relations(self) -> tuple[str, ...]:
        """
        The filter relations in which fields of this type can be used as a
        left-handside. By default, this class only supports equality. A scalar
        field type would override this method to include the `within` relation.
        """
        return 'is',

    def api_filter_schema(self, relation: str) -> JSON:
        """
        The JSONSchema describing the right-handside operand of the given filter
        relation in OpenAPI specifications when the left-handside operand is a
        field of this type.
        """
        assert relation in self.supported_filter_relations, relation
        api_type = self.api_schema
        if relation == 'is':
            return api_type
        elif relation == 'within':
            return self._api_range_schema(api_type)
        else:
            assert False, relation

    def _api_range_schema(self, api_schema: JSON) -> JSON:
        return schema.array(api_schema, minItems=2, maxItems=2)

    def _api_range_to_index(self, value: Range[T]) -> JSON:
        return {'gte': value[0], 'lte': value[1]}

    def _from_api_range(self, value: AnyJSON) -> Range[T]:
        assert isinstance(value, (list, tuple)) and len(value) == 2, value
        gte, lte = value
        return self.from_api(gte), self.from_api(lte)

    def filter(self, relation: str, values: list[AnyJSON]) -> list[T]:
        if relation == 'within':
            return list(map(self._api_range_to_index, map(self._from_api_range, values)))
        else:
            return list(map(self.to_index, values))


class PassThrough(Generic[T], FieldType[T, T]):
    allow_sorting_by_empty_lists = False

    def __init__(self, translated_type: type[T], *, es_type: str | None):
        super().__init__(translated_type, translated_type)
        self._es_type = es_type

    @property
    def es_type(self) -> str:
        return self._es_type

    def to_index(self, value: T) -> T:
        return value

    def from_index(self, value: T) -> T:
        return value


# FIXME: change the es_type for JSON to `nested`
#        https://github.com/DataBiosphere/azul/issues/2621
pass_thru_json: PassThrough[JSON] = PassThrough(JSON, es_type=None)


class NumericPassThrough(PassThrough[T]):

    @property
    def supported_filter_relations(self) -> tuple[str, ...]:
        return *super().supported_filter_relations, 'within'

    def from_api(self, value: AnyJSON) -> T:
        """
        1.0 is a valid JSONSchema `integer`

        >>> pass_thru_int.from_api(1.0)
        1

        1 is a valid JSONSchema `number`

        >>> pass_thru_float.from_api(1)
        1.0

        1.1 is not a valid JSONSchema `integer`

        >>> pass_thru_int.from_api(1.1)
        Traceback (most recent call last):
            ...
        AssertionError: 1.1

        1.1 is a valid JSONSchema `float`

        >>> pass_thru_float.from_api(1.1)
        1.1
        """
        native_value = self.native_type(value)
        assert native_value == value, value
        return native_value


pass_thru_str = PassThrough(str, es_type='keyword')
pass_thru_int = NumericPassThrough(int, es_type='long')
pass_thru_float = NumericPassThrough(float, es_type='double')
pass_thru_bool = PassThrough(bool, es_type='boolean')


class Nullable(FieldType[N | None, T]):

    def __init__(self, native_type: type[N], translated_type: type[T]) -> None:
        super().__init__(native_type | None, translated_type)

    @property
    def optional_type(self):
        native_type, none_type = get_args(self.native_type)
        assert none_type is type(None)  # noqa: E721
        return native_type

    @abstractmethod
    def to_index(self, value: N) -> T:
        raise NotImplementedError

    @abstractmethod
    def from_index(self, value: T) -> N:
        raise NotImplementedError

    @property
    def api_schema(self) -> JSON:
        return schema.nullable(schema.make(self.optional_type))


class NullableScalar(Nullable[N, T], metaclass=ABCMeta):

    def api_filter_schema(self, relation: str) -> JSON:
        if relation == 'within':
            # The LHS operand of a range relation can't be null
            api_type = schema.make(self.optional_type)
            return self._api_range_schema(api_type)
        else:
            return super().api_filter_schema(relation)

    @property
    def supported_filter_relations(self) -> tuple[str, ...]:
        return *super().supported_filter_relations, 'within'


class NullableString(Nullable[str, str]):
    # Note that the replacement values for `None` used for each data type
    # ensure that `None` values are placed at the end of a sorted list.
    null_string = '~null'
    es_type = 'keyword'

    def __init__(self):
        super().__init__(str, str)

    def to_index(self, value: str | None) -> str:
        return self.null_string if value is None else value

    def from_index(self, value: str) -> str | None:
        return None if value == self.null_string else value


null_str = NullableString()

# While Elasticsearch distinguishes between integers and floating point numbers
# in its index, JSON does not. Since all payloads to and from Elasticsearch are
# serialized as JSON we have to be prepared to get 1 back when we write 1.0.

JSONNumber = int | float

U = TypeVar('U', bound=bool | int | float)


class NullableNumber(Generic[U], NullableScalar[U, JSONNumber]):
    shadowed = True
    # Maximum int that can be represented as a 64-bit int and double IEEE
    # floating point number. This prevents loss when converting between the two.
    null_value = sys.maxsize - 1023
    assert null_value == int(float(null_value))

    def __init__(self, native_type: type[U], es_type: str) -> None:
        assert issubclass(native_type, get_args(JSONNumber))
        super().__init__(native_type, JSONNumber)
        self._es_type = es_type

    @property
    def es_type(self) -> str | None:
        return self._es_type

    def to_index(self, value: U | None) -> JSONNumber:
        if value is None:
            return self.null_value
        else:
            assert value < self.null_value, (value, self.null_value)
            return value

    def from_index(self, value: JSONNumber) -> U | None:
        if value == self.null_value:
            return None
        else:
            return self.optional_type(value)

    def from_api(self, value: AnyJSON) -> N:
        """
        1.0 is a valid JSONSchema `integer`

        >>> null_int.from_api(1.0)
        1

        1 is a valid JSONSchema `number`

        >>> pass_thru_float.from_api(1)
        1.0

        1.1 is not a valid JSONSchema `integer`

        >>> null_int.from_api(1.1)
        Traceback (most recent call last):
            ...
        AssertionError: 1.1

        1.1 is a valid JSONSchema `float`

        >>> pass_thru_float.from_api(1.1)
        1.1
        """
        native_value = self.optional_type(value)
        assert native_value == value, value
        return native_value


null_int = NullableNumber(int, 'long')
null_float = NullableNumber(float, 'double')


class NullableBool(NullableNumber[bool]):
    shadowed = False

    def __init__(self):
        super().__init__(bool, 'boolean')

    def to_index(self, value: bool | None) -> JSONNumber:
        value = {False: 0, True: 1, None: None}[value]
        return super().to_index(value)

    def from_index(self, value: JSONNumber) -> bool | None:
        value = super().from_index(value)
        return {0: False, 1: True, None: None}[value]

    @property
    def supported_filter_relations(self) -> tuple[str, ...]:
        return 'is',  # no point in supporting range relation


null_bool = NullableBool()


class NullableDateTime(Nullable[str, str]):
    es_type = 'date'
    null = format_dcp2_datetime(datetime(9999, 1, 1, tzinfo=timezone.utc))

    def to_index(self, value: str | None) -> str:
        if value is None:
            return self.null
        else:
            parse_dcp2_datetime(value)
            return value

    def from_index(self, value: str) -> str | None:
        if value == self.null:
            return None
        else:
            return value


null_datetime: NullableDateTime = NullableDateTime(str, str)


class Nested(PassThrough[JSON]):
    properties: Mapping[str, FieldType]
    agg_property: str

    def __init__(self, **properties):
        super().__init__(JSON, es_type='nested')
        self.agg_property = first(properties.keys())
        self.properties = properties

    def api_filter_schema(self, relation: str) -> JSON:
        assert relation == 'is'
        properties, required = {}, []
        for field, field_type in self.properties.items():
            properties[field] = field_type.api_filter_schema(relation)
            if not isinstance(field_type, Nullable):
                required.append(field)
        kwargs = dict(additionalProperties=False)
        if required:
            kwargs['required'] = required
        return schema.object(properties=properties, **kwargs)

    def filter(self, relation: str, values: list[JSON]) -> list[JSON]:
        nested_object = one(values)
        assert isinstance(nested_object, dict)
        query_filters = {}
        for nested_field, nested_value in nested_object.items():
            nested_type = self.properties[nested_field]
            to_index = nested_type.to_index
            value = one(values)[nested_field]
            query_filters[nested_field] = to_index(value)
        return [query_filters]


class ClosedRange(Generic[P], FieldType[Range[P], JSON]):

    def __init__(self, ends_type: FieldType[P, P]):
        super().__init__(Range[P], JSON)
        self.ends_type = ends_type

    @property
    def es_type(self) -> str | None:
        return None

    def to_index(self, value: Range[P]) -> JSON:
        return self._api_range_to_index(value)

    def from_index(self, value: JSON) -> Range[P]:
        return value['gte'], value['lte']

    @property
    def api_schema(self):
        return self._api_range_schema(self.ends_type.api_schema)

    @property
    def supported_filter_relations(self) -> tuple[str, ...]:
        return 'is', 'within', 'contains', 'intersects'

    def api_filter_schema(self, relation: str) -> JSON:
        if relation == 'contains':
            # A range can contain a range or a value
            return schema.union(self.ends_type.api_schema, self.api_schema)
        else:
            return self.api_schema

    def from_api(self, value: AnyJSON) -> Range[P]:
        return self.ends_type._from_api_range(value)

    def filter(self, relation: str, values: list[AnyJSON]) -> list[JSON]:
        result = []
        for value in values:
            if isinstance(value, list):
                pass
            elif relation == 'contains' and isinstance(value, reify(PrimitiveJSON)):
                value = [value, value]
            else:
                assert False, (relation, value)
            result.append(self.to_index(self.from_api(value)))
        return result


FieldTypes4 = Mapping[str, FieldType] | Sequence[FieldType] | FieldType
FieldTypes3 = Mapping[str, FieldTypes4] | Sequence[FieldType] | FieldType
FieldTypes2 = Mapping[str, FieldTypes3] | Sequence[FieldType] | FieldType
FieldTypes1 = Mapping[str, FieldTypes2] | Sequence[FieldType] | FieldType
FieldTypes = Mapping[str, FieldTypes1]
CataloguedFieldTypes = Mapping[CatalogName, FieldTypes]
