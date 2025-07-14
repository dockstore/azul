from abc import (
    ABCMeta,
    abstractmethod,
)
from datetime import (
    datetime,
    timezone,
)
import sys
from types import (
    UnionType,
)
from typing import (
    ClassVar,
    Final,
    Iterable,
    Mapping,
    Sequence,
    TypeAliasType,
    TypedDict,
    cast,
)

from more_itertools import (
    first,
    one,
)

from azul import (
    CatalogName,
    cached_property,
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
    JSON,
    PrimitiveJSON,
    reify,
)

# A type variable named ``N`` denotes the native type of a field in documents as
# they are being created by a transformer or processed by an aggregator.
#
# A type variable named ``X`` denotes the type of a field in a document just
# before it's being written to the index. Think "index type".

#: The static (build time) type of a document field value
#:
type Form[T] = type[T] | TypeAliasType | UnionType

#: The upper bound on the type of field values stored in the index:
#:
#: Note that while ``IndexRange`` *is* assignable to JSON, ``mypy`` doesn't
#: realize that hence the need for the union in the definition.
#:
type IndexForm = AnyJSON | IndexRange


#: The Elasticsearch index representation of ranges along with a factory
#:
class IndexRange[X: IndexForm](TypedDict):
    gte: X
    lte: X


def index_range[X: IndexForm](gte: X, lte: X) -> IndexRange[X]:
    return dict(gte=gte, lte=lte)


#: The native and API representations of ranges
#:
type Range[E] = tuple[E, E]
type ApiRange = Range[AnyJSON] | list[AnyJSON]

#: While Elasticsearch distinguishes between integers and floating point numbers
#: in its index, JSON does not. Since all payloads to and from Elasticsearch are
#: serialized as JSON we have to be prepared to get 1 back when we write 1.0.
#:
type JSONNumber = int | float


class FieldType[N, X: IndexForm](metaclass=ABCMeta):
    shadowed: ClassVar[bool] = False
    es_sort_mode: ClassVar[str] = 'min'
    allow_sorting_by_empty_lists: ClassVar[bool] = True

    def __init__(self, native_form: Form[N], index_form: Form[X]):
        self.native_form: Final[Form[N]] = native_form
        self.index_form: Final[Form[X]] = index_form

    @cached_property
    def native_types(self) -> tuple[type, ...]:
        """
        The possible runtime (reified) types of the value of document fields
        of this type.
        """
        return reify(self.native_form)

    @cached_property
    def index_types(self) -> tuple[type, ...]:
        return reify(self.index_form)

    @property
    @abstractmethod
    def es_type(self) -> str | None:
        raise NotImplementedError

    @abstractmethod
    def to_index(self, value: N) -> X:
        raise NotImplementedError

    @abstractmethod
    def from_index(self, value: X) -> N:
        raise NotImplementedError

    def to_tsv(self, value: N) -> str:
        return '' if value is None else str(value)

    @property
    def api_schema(self) -> JSON:
        """
        The JSONSchema describing fields of this type in OpenAPI specifications.
        """
        return schema.coalesce(self.native_types)

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
        assert isinstance(value, self.native_types), (value, self)
        return cast(N, value)

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
        if relation == 'is':
            return self.api_schema
        elif relation == 'within':
            return self._api_range_schema(self.api_schema)
        else:
            assert False, relation

    def _api_range_schema(self, api_schema: JSON) -> JSON:
        return schema.array(api_schema, minItems=2, maxItems=2)

    def _range_to_index(self, value: Range[N]) -> IndexRange[X]:
        gte, lte = value
        return index_range(self.to_index(gte), self.to_index(lte))

    def _from_api_range(self, value: AnyJSON) -> Range[N]:
        assert isinstance(value, (list, tuple)) and len(value) == 2, value
        gte, lte = value
        return self.from_api(gte), self.from_api(lte)

    def filter(self,
               relation: str,
               values: Iterable[AnyJSON | ApiRange]
               ) -> Iterable[X | IndexRange[X]]:
        if relation == 'within':
            return list(map(self._range_to_index, map(self._from_api_range, values)))
        else:
            return list(map(self.to_index, map(self.from_api, values)))


class PassThrough[T: AnyJSON](FieldType[T, T]):
    allow_sorting_by_empty_lists = False

    def __init__(self, type: Form[T], *, es_type: str | None):
        super().__init__(type, type)
        self._es_type = es_type

    @property
    def es_type(self) -> str | None:
        return self._es_type

    def to_index(self, value: T) -> T:
        return value

    def from_index(self, value: T) -> T:
        return value


# FIXME: change the es_type for JSON to `nested`
#        https://github.com/DataBiosphere/azul/issues/2621
pass_thru_json: PassThrough[JSON] = PassThrough(JSON, es_type=None)


class NumericPassThrough[T: JSONNumber](PassThrough[T]):

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
        assert isinstance(value, (int, float))
        native_type, = self.native_types
        native_value = native_type(value)
        assert native_value == value, value
        assert isinstance(native_value, native_type)
        return cast(T, native_value)


pass_thru_str = PassThrough(str, es_type='keyword')
pass_thru_int = NumericPassThrough(int, es_type='long')
pass_thru_float = NumericPassThrough(float, es_type='double')
pass_thru_bool = PassThrough(bool, es_type='boolean')


class Nullable[N, X: IndexForm](FieldType[N | None, X], metaclass=ABCMeta):

    def __init__(self, native_type: type[N], translated_from: Form[X]) -> None:
        self.native_type: Final[type[N]] = native_type
        super().__init__(native_type | None, translated_from)

    @property
    def api_schema(self) -> JSON:
        return schema.nullable(schema.make(self.native_type))


class NullableScalar[N, X: IndexForm](Nullable[N, X], metaclass=ABCMeta):

    def api_filter_schema(self, relation: str) -> JSON:
        if relation == 'within':
            # The LHS operand of a range relation can't be null
            api_type = schema.make(self.native_type)
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


class NullableNumber[U: bool | int | float](NullableScalar[U, JSONNumber]):
    shadowed = True
    # Maximum int that can be represented as a 64-bit int and double IEEE
    # floating point number. This prevents loss when converting between the two.
    null_value = sys.maxsize - 1023
    assert null_value == int(float(null_value))

    def __init__(self, native_type: type[U], es_type: str) -> None:
        assert native_type in (bool, int, float)
        super().__init__(native_type, JSONNumber)
        self._es_type = es_type

    @property
    def es_type(self) -> str | None:
        return self._es_type

    def to_index(self, value: U | None) -> JSONNumber:
        if value is None:
            return self.null_value
        elif value is False:
            return 0
        elif value is True:
            return 1
        else:
            assert value < self.null_value, (value, self.null_value)
            return value

    def from_index(self, value: JSONNumber) -> U | None:
        if value == self.null_value:
            return None
        else:
            return self._from_json(value)

    def _from_json(self, value: AnyJSON) -> U | None:
        assert isinstance(value, (int, float))
        native_type = self.native_type
        native_value = native_type(value)
        assert native_value == value, value
        assert isinstance(native_value, native_type)
        return native_value

    def from_api(self, value: AnyJSON) -> U | None:
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
        if value is None:
            return None
        else:
            return self._from_json(value)


null_int = NullableNumber(int, 'long')
null_float = NullableNumber(float, 'double')


class NullableBool(NullableNumber[bool]):
    shadowed = False

    def __init__(self):
        super().__init__(bool, 'boolean')

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
        kwargs: dict[str, AnyJSON] = dict(additionalProperties=False)
        if required:
            kwargs['required'] = required
        return schema.object(properties=properties, **kwargs)

    def filter(self,
               relation: str,
               values: Iterable[AnyJSON | ApiRange]
               ) -> Iterable[JSON | IndexRange[JSON]]:
        nested_object = one(values)
        assert isinstance(nested_object, dict)
        query_filters = {}
        for nested_field, nested_value in nested_object.items():
            nested_type = self.properties[nested_field]
            to_index = nested_type.to_index
            query_filters[nested_field] = to_index(nested_value)
        return [query_filters]


class ClosedRange[N: PrimitiveJSON, X: IndexForm](FieldType[Range[N], IndexRange[X]]):

    def __init__(self, ends_type: FieldType[N, X]):
        super().__init__(reify(Range[N]), reify(JSON))
        self.ends_type = ends_type

    @property
    def es_type(self) -> str | None:
        return None

    def to_index(self, value: Range[N]) -> IndexRange[X]:
        gte, lte = value
        to_index = self.ends_type.to_index
        return index_range(to_index(gte), to_index(lte))

    def from_index(self, value: IndexRange[X]) -> Range[N]:
        from_index = self.ends_type.from_index
        return from_index(value['gte']), from_index(value['lte'])

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

    def from_api(self, value: AnyJSON) -> Range[N]:
        return self.ends_type._from_api_range(value)

    def filter(self,
               relation: str,
               values: Iterable[AnyJSON]
               ) -> Iterable[IndexRange[X]]:
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


type FieldTypes1 = Mapping[str, FieldTypes1] | Sequence[FieldType] | FieldType
type FieldTypes = Mapping[str, FieldTypes1]
type CataloguedFieldTypes = Mapping[CatalogName, FieldTypes]
