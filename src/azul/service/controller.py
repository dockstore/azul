import json
from typing import (
    Any,
    Callable,
    Mapping,
)
import urllib.parse

from chalice import (
    BadRequestError as BRE,
    NotFoundError,
)
from more_itertools import (
    one,
)

from azul import (
    CatalogName,
    R,
    RequirementError,
    config,
    mutable_furl,
    require,
)
from azul.auth import (
    Authentication,
)
from azul.indexer.field import (
    Nested,
)
from azul.openapi import (
    application_json,
    format_description as fd,
    params,
    schema,
)
from azul.plugins.metadata.hca.indexer.transform import (
    value_and_unit,
)
from azul.service import (
    Filters,
    FiltersJSON,
    normalize_filters,
    parse_filters,
    validate_filters,
)
from azul.service.source_controller import (
    SourceController,
)
from azul.strings import (
    pluralize,
)
from azul.types import (
    JSON,
    MutableJSON,
    PrimitiveJSON,
    reify,
)


class ServiceController(SourceController):

    def file_url(self,
                 *,
                 catalog: CatalogName,
                 file_uuid: str,
                 fetch: bool = True,
                 **params: str
                 ) -> mutable_furl:
        file_uuid = urllib.parse.quote(file_uuid, safe='')
        path = '/fetch/repository/files/{file_uuid}' if fetch else '/repository/files/{file_uuid}'
        url = self.app.base_url.add(path=path.format(file_uuid=file_uuid))
        return url.set(args=dict(catalog=catalog, **params))

    file_fqid_parameters_spec = [
        params.path(
            'file_uuid',
            str,
            description='The UUID of the file to be returned.'),
        params.query(
            'version',
            schema.optional(str),
            description=fd('''
                The version of the file to be returned. File versions are opaque
                strings with only one documented property: they can be
                lexicographically compared with each other in order to determine
                which version is more recent. If this parameter is omitted then the
                most recent version of the file is returned.
            ''')
        )
    ]

    def _hoist_parameters(self, query_params, request):
        if request.method in ('POST', 'PUT'):
            body = request.json_body
            if body is not None:
                if not isinstance(body, dict):
                    raise BRE('Request body is not a JSON object')
                elif body.keys() & query_params.keys():
                    raise BRE('Conflicting keys between body and query parameters')
                else:
                    query_params.update(body)

    def parameter_hoisting_note(self,
                                method: str,
                                endpoint: str,
                                equivalent_method: str
                                ) -> str:
        return fd('''
            Any of the query parameters documented below can alternatively be passed
            as a property of a JSON object in the body of the request. This can be
            useful in case the value of the `filters` query parameter causes the URL
            to exceed the maximum length of 8192 characters, resulting in a 413
            Request Entity Too Large response.

            The request `%s %s?filters={…}`, for example, is equivalent to  `%s %s`
            with the body `{"filters": "{…}"}` in which any double quotes or
            backslash characters inside `…` are escaped with another backslash. That
            escaping is the requisite procedure for embedding one JSON structure
            inside another.
        ''' % (method, endpoint, equivalent_method, endpoint))

    @property
    def catalog_param_spec(self):
        return params.query(
            'catalog',
            schema.optional(schema.default(self.app.catalog,
                                           form=schema.enum(*config.catalogs))),
            description='The name of the catalog to query.')

    @property
    def filters_param_spec(self):
        types = self.app.repository_controller.field_types(self.app.catalog)

        def _filter_schema(field_type):
            operators = field_type.supported_filter_operators

            def filter_schema(operator):
                return schema.object(
                    properties={
                        operator: field_type.api_filter_values_schema(operator)
                    },
                    required=[operator],
                    additionalProperties=False
                )

            if len(operators) == 1:
                return filter_schema(one(operators))
            else:
                return {'oneOf': list(map(filter_schema, operators))}

        return params.query(
            'filters',
            schema.optional(application_json(schema.object(
                default='{}',
                example={'cellCount': {'within': [[10000, 1000000000]]}},
                properties={
                    field: _filter_schema(types[field])
                    for field in self.app.fields
                }
            ))),
            description=fd('''
                Criteria to filter entities from the search results.

                Each filter consists of a field name, an operator, and an array of field
                values. The available operators are "is", "within", "contains", and
                "intersects". Multiple filters are combined using "and" logic. For an
                entity to be included in the response, it must match all filters. How
                multiple field values within a single filter are combined depends on the
                operator.

                For the "is" operator, multiple values are combined using "or" logic.
                For example, `{"fileFormat": {"is": ["fastq", "fastq.gz"]}}` selects
                entities where the file format is either "fastq" or "fastq.gz". For the
                "within", "intersects", and "contains" operators, the field values must
                come in nested pairs specifying upper and lower bounds, and multiple
                pairs are combined using "and" logic. For example, `{"donorCount":
                {"within": [[1,5], [5,10]]}}` selects entities whose donor organism
                count falls within both ranges, i.e., is exactly 5.

                The accessions field supports filtering for a specific accession and/or
                namespace within a project. For example, `{"accessions": {"is": [
                {"namespace":"array_express"}]}}` will filter for projects that have an
                `array_express` accession. Similarly, `{"accessions": {"is": [
                {"accession":"ERP112843"}]}}` will filter for projects that have the
                accession `ERP112843` while `{"accessions": {"is": [
                {"namespace":"array_express", "accession": "E-AAAA-00"}]}}` will filter
                for projects that match both values.

                The organismAge field is special in that it contains two property keys:
                value and unit. For example, `{"organismAge": {"is": [{"value": "20",
                "unit": "year"}]}}`. Both keys are required. `{"organismAge": {"is":
                [null]}}` selects entities that have no organism age.''' + f'''

                Supported field names are: {', '.join(self.app.fields)}
            ''')
        )

    def validate_json_param(self, name: str, value: str) -> MutableJSON:
        try:
            return json.loads(value)
        except json.decoder.JSONDecodeError:
            raise BRE(f'The {name!r} parameter is not valid JSON')

    def validate_organism_age_filter(self, values):
        for value in values:
            try:
                value_and_unit.to_index(value)
            except AssertionError as e:
                if R.caused(e):
                    raise R.propagate(e, BRE)
                else:
                    raise

    def validate_field(self, field: str, *, include_synthetic: bool = False):
        fields = self.app.fields if include_synthetic else self.app.organic_fields
        if field not in fields:
            raise BRE(f'Unknown field `{field}`')

    def validate_filters(self, filters):
        filters = self.validate_json_param('filters', filters)
        if type(filters) is not dict:
            raise BRE('The `filters` parameter must be a dictionary')
        field_types = self.app.repository_controller.field_types(self.app.catalog)
        special_fields = self.app.metadata_plugin.special_fields
        accessibility_fields = {
            special_fields.source_id.name,
            special_fields.accessible.name
        }
        for field, filter_ in filters.items():
            self.validate_field(field, include_synthetic=True)
            try:
                operator, values = one(filter_.items())
            except Exception:
                raise BRE(f'The `filters` parameter entry for `{field}` '
                          f'must be a single-item dictionary')
            else:
                if field in accessibility_fields:
                    valid_operators = ('is',)
                    disallow_null = True
                else:
                    valid_operators = ('is', 'contains', 'within', 'intersects')
                    disallow_null = False
                if operator in valid_operators:
                    if not isinstance(values, list):
                        raise BRE(f'The value of the `{operator}` operator in the `filters` '
                                  f'parameter entry for `{field}` is not a list')
                    if disallow_null and None in values:
                        raise BRE(f'The `{field}` field does not support null values')
                else:
                    raise BRE(f'The operator in the `filters` parameter entry '
                              f'for `{field}` must be one of {valid_operators}')
                if operator == 'is':
                    value_types = reify(JSON | PrimitiveJSON)
                    if not all(isinstance(value, value_types) for value in values):
                        raise BRE(f'The value of the `is` operator in the `filters` '
                                  f'parameter entry for `{field}` is invalid')
                if field == 'organismAge':
                    self.validate_organism_age_filter(values)
                field_type = field_types[field]
                if isinstance(field_type, Nested):
                    if operator != 'is':
                        raise BRE(f'The field `{field}` can only be filtered by the `is` operator')
                    try:
                        nested = one(values)
                    except ValueError:
                        raise BRE(f'The value of the `is` operator in the `filters` '
                                  f'parameter entry for `{field}` is not a single-item list')
                    try:
                        require(isinstance(nested, dict))
                    except AssertionError as e:
                        if R.caused(e):
                            raise BRE(f'The value of the `is` operator in the `filters` '
                                      f'parameter entry for `{field}` must contain a dictionary')
                        else:
                            raise
                    extra_props = nested.keys() - field_type.properties.keys()
                    if extra_props:
                        raise BRE(f'The value of the `is` operator in the `filters` '
                                  f'parameter entry for `{field}` has invalid properties `{extra_props}`')

    def get_filters(self,
                    catalog: CatalogName,
                    authentication: Authentication | None,
                    filters: str | None = None
                    ) -> Filters:
        return Filters(explicit=self._parse_filters(filters),
                       source_ids=self._list_source_ids(catalog, authentication))

    def _parse_filters(self, filters: str | None) -> FiltersJSON:
        try:
            return normalize_filters(validate_filters(parse_filters(filters)))
        except AssertionError as e:
            if R.caused(e):
                raise R.propagate(e, BRE)
            else:
                raise


def validate_catalog(catalog):
    try:
        config.Catalog.validate_name(catalog)
    except AssertionError as e:
        if R.caused(e):
            raise R.propagate(e, BRE)
        else:
            raise
    else:
        if catalog not in config.catalogs:
            raise NotFoundError(f'Catalog name {catalog!r} does not exist. '
                                f'Must be one of {set(config.catalogs)}.')


class Mandatory:
    """
    Validation wrapper signifying that a parameter is mandatory.
    """

    def __init__(self, validator: Callable) -> None:
        super().__init__()
        self._validator = validator

    def __call__(self, param):
        return self._validator(param)


def validate_params(query_params: Mapping[str, str],
                    allow_extra_params: bool = False,
                    **validators: Callable[[Any], Any]) -> None:
    """
    Validates request query parameters for web-service API.

    :param query_params: the parameters to be validated

    :param allow_extra_params:

        When False, only parameters specified via '**validators' are accepted,
        and validation fails if additional parameters are present. When True,
        additional parameters are allowed but their value is not validated.

    :param validators:

        A dictionary mapping the name of a parameter to a function that will be
        used to validate the parameter if it is provided. The callable will be
        called with a single argument, the parameter value to be validated, and
        is expected to raise ValueError, TypeError or azul.RequirementError if
        the value is invalid. Only these exceptions will yield a 4xx status
        response, all other exceptions will yield a 500 status response. If the
        validator is an instance of `Mandatory`, then validation will fail if
        its corresponding parameter is not provided.

    >>> validate_params({'order': 'asc'}, order=str)

    >>> validate_params({'size': 'foo'}, size=int)
    Traceback (most recent call last):
        ...
    chalice.app.BadRequestError: Invalid value for `size`

    >>> validate_params({'order': 'asc', 'foo': 'bar'}, order=str)
    Traceback (most recent call last):
        ...
    chalice.app.BadRequestError: Unknown query parameter `foo`

    >>> validate_params({'order': 'asc', 'foo': 'bar'}, order=str, allow_extra_params=True)

    >>> validate_params({}, foo=str)

    >>> validate_params({}, foo=Mandatory(str))
    Traceback (most recent call last):
        ...
    chalice.app.BadRequestError: Missing required query parameter `foo`

    """

    def fmt_error(err_description, params):
        # Sorting is to produce a deterministic error message
        joined = ', '.join(f'`{p}`' for p in sorted(params))
        return f'{err_description} {pluralize("query parameter", len(params))} {joined}'

    provided_params = query_params.keys()
    validation_params = validators.keys()
    mandatory_params = {
        param_name
        for param_name, validator in validators.items()
        if isinstance(validator, Mandatory)
    }

    if not allow_extra_params:
        extra_params = provided_params - validation_params
        if extra_params:
            raise BRE(fmt_error('Unknown', extra_params))

    if mandatory_params:
        missing_params = mandatory_params - provided_params
        if missing_params:
            raise BRE(fmt_error('Missing required', missing_params))

    for param_name, validator in validators.items():
        try:
            param_value = query_params[param_name]
        except KeyError:
            pass
        else:
            try:
                validator(param_value)
            except (TypeError, ValueError, RequirementError):
                raise BRE(f'Invalid value for `{param_name}`')
