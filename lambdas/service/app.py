from collections.abc import (
    Sequence,
)
import json
import logging.config
import urllib.parse

import attr
from chalice import (
    BadRequestError as BRE,
    ChaliceViewError,
    UnauthorizedError,
)
from furl import (
    furl,
)
from more_itertools import (
    one,
)

from azul import (
    CatalogName,
    R,
    cache,
    cached_property,
    config,
    mutable_furl,
    require,
)
from azul.auth import (
    OAuth2,
)
from azul.collections import (
    OrderedSet,
)
from azul.health import (
    HealthApp,
)
from azul.indexer.document import (
    EntityType,
)
from azul.indexer.field import (
    Nested,
)
from azul.logging import (
    configure_app_logging,
)
from azul.openapi import (
    format_description as fd,
)
from azul.plugins import (
    ManifestFormat,
    MetadataPlugin,
    RepositoryPlugin,
)
from azul.plugins.metadata.hca.indexer.transform import (
    value_and_unit,
)
from azul.service.catalog_controller import (
    CatalogController,
)
from azul.service.download_controller import (
    DownloadController,
)
from azul.service.drs_controller import (
    DRSController,
)
from azul.service.elasticsearch_service import (
    Pagination,
)
from azul.service.manifest_controller import (
    ManifestController,
)
from azul.service.repository_controller import (
    RepositoryController,
)
from azul.types import (
    JSON,
    MutableJSON,
    PrimitiveJSON,
    reify,
)

log = logging.getLogger(__name__)

spec = {
    'openapi': '3.0.1',
    'info': {
        'title': config.service_name,
        # The version property should be updated in any PR connected to an issue
        # labeled `API`. Increment the major version for backwards incompatible
        # changes and reset the minor version to zero. Otherwise, increment only
        # the minor version for backwards compatible changes. A backwards
        # compatible change is one that does not require updates to clients.
        'version': '15.1',
        'description': fd(f'''
            # Overview

            Azul is a REST web service for querying metadata associated with
            both experimental and analysis data from a data repository. In order
            to deliver response times that make it suitable for interactive use
            cases, the set of metadata properties that it exposes for sorting,
            filtering, and aggregation is limited. Azul provides a uniform view
            of the metadata over a range of diverse schemas, effectively
            shielding clients from changes in the schemas as they occur over
            time. It does so, however, at the expense of detail in the set of
            metadata properties it exposes and in the accuracy with which it
            aggregates them.

            Azul denormalizes and aggregates metadata into several different
            indices for selected entity types. Metadata entities can be queried
            using the [Index](#operations-tag-Index) endpoints.

            A set of indices forms a catalog. There is a default catalog called
            `{config.default_catalog}` which will be used unless a
            different catalog name is specified using the `catalog` query
            parameter. Metadata from different catalogs is completely
            independent: a response obtained by querying one catalog does not
            necessarily correlate to a response obtained by querying another
            one. Two catalogs can contain metadata from the same sources or
            different sources. It is only guaranteed that the body of a
            response by any given endpoint adheres to one schema,
            independently of which catalog was specified in the request.

            Azul provides the ability to download data and metadata via the
            [Manifests](#operations-tag-Manifests) endpoints. The
            `{ManifestFormat.curl.value}` format manifests can be used to
            download data files. Other formats provide various views of the
            metadata. Manifests can be generated for a selection of files using
            filters. These filters are interchangeable with the filters used by
            the [Index](#operations-tag-Index) endpoints.

            Azul also provides a [summary](#operations-Index-get_index_summary)
            view of indexed data.

            ## Data model

            Any index, when queried, returns a JSON array of hits. Each hit
            represents a metadata entity. Nested in each hit is a summary of the
            properties of entities associated with the hit. An entity is
            associated either by a direct edge in the original metadata graph,
            or indirectly as a series of edges. The nested properties are
            grouped by the type of the associated entity. The properties of all
            data files associated with a particular sample, for example, are
            listed under `hits[*].files` in a `/index/samples` response. It is
            important to note that while each _hit_ represents a discrete
            entity, the properties nested within that hit are the result of an
            aggregation over potentially many associated entities.

            To illustrate this, consider a data file that is part of two
            projects (a project is a group of related experiments, typically by
            one laboratory, institution or consortium). Querying the `files`
            index for this file yields a hit looking something like:

            ```
            {{
                "projects": [
                    {{
                        "projectTitle": "Project One"
                        "laboratory": ...,
                        ...
                    }},
                    {{
                        "projectTitle": "Project Two"
                        "laboratory": ...,
                        ...
                    }}
                ],
                "files": [
                    {{
                        "format": "pdf",
                        "name": "Team description.pdf",
                        ...
                    }}
                ]
            }}
            ```

            This example hit contains two kinds of nested entities (a hit in an
            actual Azul response will contain more): There are the two projects
            entities, and the file itself. These nested entities contain
            selected metadata properties extracted in a consistent way. This
            makes filtering and sorting simple.

            Also notice that there is only one file. When querying a particular
            index, the corresponding entity will always be a singleton like
            this.
        ''')
    },
    'tags': [
        {
            'name': 'Index',
            'description': fd('''
                Query the indices for entities of interest
            ''')
        },
        {
            'name': 'Manifests',
            'description': fd('''
                Complete listing of files matching a given filter in TSV and
                other formats
            ''')
        },
        {
            'name': 'Repository',
            'description': fd('''
                Access to data files in the underlying repository
            ''')
        },
        {
            'name': 'DSS',
            'description': fd('''
                Access to files maintained in the Data Store
            ''')
        },
        {
            'name': 'DRS',
            'description': fd('''
                DRS-compliant proxy of the underlying repository
            ''')
        },
        {
            'name': 'Auxiliary',
            'description': fd('''
                Describes various aspects of the Azul service
            ''')
        },
        {
            'name': 'Deprecated',
            'description': fd('''
                Endpoints that should not be used and that will be removed
            ''')
        }
    ]
}


class ServiceApp(HealthApp):

    def spec(self) -> JSON:
        return {
            **super().spec(),
            **self._oauth2_spec()
        }

    def _oauth2_spec(self) -> JSON:
        scopes = ('email',)
        return {
            'components': {
                'securitySchemes': {
                    self.app_name: {
                        'type': 'oauth2',
                        'flows': {
                            'implicit': {
                                'authorizationUrl': 'https://accounts.google.com/o/oauth2/auth',
                                'scopes': {scope: scope for scope in scopes}
                            }
                        }
                    }
                }
            },
            'security': [
                {},
                {self.app_name: scopes}
            ]
        }

    @property
    def drs_controller(self) -> DRSController:
        return DRSController(app=self, file_url_func=self.file_url)

    @cached_property
    def catalog_controller(self) -> CatalogController:
        return CatalogController(app=self, file_url_func=self.file_url)

    @cached_property
    def repository_controller(self) -> RepositoryController:
        return RepositoryController(app=self, file_url_func=self.file_url)

    @cached_property
    def download_controller(self) -> DownloadController:
        return DownloadController(app=self, file_url_func=self.file_url)

    @cached_property
    def manifest_controller(self) -> ManifestController:
        return ManifestController(app=self,
                                  file_url_func=self.file_url,
                                  manifest_url_func=self.manifest_url)

    @property
    def metadata_plugin(self) -> MetadataPlugin:
        return self._metadata_plugin(self.catalog)

    @cache
    def _metadata_plugin(self, catalog: CatalogName):
        return MetadataPlugin.load(catalog).create()

    @property
    def repository_plugin(self) -> RepositoryPlugin:
        return self._repository_plugin(self.catalog)

    @cache
    def _repository_plugin(self, catalog: CatalogName):
        return RepositoryPlugin.load(catalog).create(catalog)

    @property
    def fields(self) -> Sequence[str]:
        organic, synthetic = self.organic_fields, self.synthetic_fields
        all = OrderedSet(organic)
        all.update(synthetic)
        assert len(all) == len(organic) + len(synthetic)
        return tuple(all)

    @property
    def organic_fields(self) -> Sequence[str]:
        return sorted(self.metadata_plugin.field_mapping.keys())

    @property
    def synthetic_fields(self) -> Sequence[str]:
        return self.metadata_plugin.special_fields.accessible.name,

    def __init__(self):
        super().__init__(app_name=config.service_name,
                         globals=globals(),
                         spec=spec)

    @attr.s(kw_only=True, auto_attribs=True, frozen=True)
    class Pagination(Pagination):
        self_url: furl

        def link(self, *, previous: bool, **params: str) -> furl | None:
            search_key = self.search_before if previous else self.search_after
            if search_key is None:
                return None
            else:
                before_or_after = 'before' if previous else 'after'
                params = {
                    **params,
                    f'search_{before_or_after}': json.dumps(search_key),
                    'sort': self.sort,
                    'order': self.order,
                    'size': self.size
                }
            return furl(url=self.self_url, args=params)

    def get_pagination(self, entity_type: str) -> Pagination:
        default_sorting = self.metadata_plugin.exposed_indices[entity_type]
        params = self.current_request.query_params or {}
        sb, sa = params.get('search_before'), params.get('search_after')
        if sb is None:
            if sa is not None:
                sa = tuple(json.loads(sa))
        else:
            if sa is None:
                sb = tuple(json.loads(sb))
            else:
                raise BRE('Only one of search_after or search_before may be set')
        try:
            return self.Pagination(order=params.get('order', default_sorting.order),
                                   size=int(params.get('size', '10')),
                                   sort=params.get('sort', default_sorting.field_name),
                                   search_before=sb,
                                   search_after=sa,
                                   self_url=self.self_url)
        except AssertionError as e:
            if R.caused(e):
                raise R.propagate(e, ChaliceViewError)
            else:
                raise

    def file_url(self,
                 *,
                 catalog: CatalogName,
                 file_uuid: str,
                 fetch: bool = True,
                 **params: str
                 ) -> mutable_furl:
        file_uuid = urllib.parse.quote(file_uuid, safe='')
        view_function = fetch_repository_files if fetch else repository_files
        path = one(view_function.path)
        url = self.base_url.add(path=path.format(file_uuid=file_uuid))
        return url.set(args=dict(catalog=catalog, **params))

    def _authenticate(self) -> OAuth2 | None:
        try:
            header = self.current_request.headers['Authorization']
        except KeyError:
            return None
        else:
            try:
                auth_type, auth_token = header.split()
            except ValueError:
                raise UnauthorizedError(header)
            else:
                if auth_type.lower() == 'bearer':
                    return OAuth2(auth_token)
                else:
                    raise UnauthorizedError(header)

    def manifest_url(self,
                     *,
                     fetch: bool,
                     token_or_key: str | None = None,
                     **params: str
                     ) -> mutable_furl:
        if token_or_key is None:
            handler = fetch_file_manifest if fetch else file_manifest
            path = one(handler.path)
        else:
            handler = fetch_file_manifest_with_token if fetch else file_manifest_with_token
            path: str = one(handler.path)
            path = path.format(token=token_or_key)
        url = self.base_url.add(path=path)
        return url.set(args=params)


app = ServiceApp()
configure_app_logging(app, log)

globals().update(app.default_routes())


def validate_entity_type(entity_type: str):
    entity_types = app.metadata_plugin.exposed_indices.keys()
    if entity_type not in entity_types:
        raise BRE(f'Entity type {entity_type!r} is invalid for catalog '
                  f'{app.catalog!r}. Must be one of {set(entity_types)}.')


min_page_size = 1


def validate_size(entity_type: EntityType, size: str):
    sorting = app.metadata_plugin.exposed_indices[entity_type]
    try:
        size = int(size)
    except BaseException:
        raise BRE('Invalid value for parameter `size`')
    else:
        if size > sorting.max_page_size:
            raise BRE(f'Invalid value for parameter `size`, '
                      f'must not be greater than {sorting.max_page_size}')
        elif size < min_page_size:
            raise BRE('Invalid value for parameter `size`, must be greater than 0')


def validate_filters(filters):
    filters = validate_json_param('filters', filters)
    if type(filters) is not dict:
        raise BRE('The `filters` parameter must be a dictionary')
    field_types = app.repository_controller.field_types(app.catalog)
    special_fields = app.metadata_plugin.special_fields
    accessibility_fields = {
        special_fields.source_id.name,
        special_fields.accessible.name
    }
    for field, filter_ in filters.items():
        validate_field(field, include_synthetic=True)
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
                validate_organism_age_filter(values)
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


def validate_organism_age_filter(values):
    for value in values:
        try:
            value_and_unit.to_index(value)
        except AssertionError as e:
            if R.caused(e):
                raise R.propagate(e, BRE)
            else:
                raise


def validate_field(field: str, *, include_synthetic: bool = False):
    fields = app.fields if include_synthetic else app.organic_fields
    if field not in fields:
        raise BRE(f'Unknown field `{field}`')


def validate_manifest_format(format: str):
    supported_formats = {f.value for f in app.metadata_plugin.manifest_formats}
    try:
        ManifestFormat(format)
    except ValueError:
        raise BRE(f'Unknown manifest format `{format}`. '
                  f'Must be one of {supported_formats}')
    else:
        if format not in supported_formats:
            raise BRE(f'Manifest format `{format}` is not supported for '
                      f'catalog {app.catalog}. Must be one of {supported_formats}')


def validate_order(order: str):
    supported_orders = ('asc', 'desc')
    if order not in supported_orders:
        raise BRE(f'Unknown order `{order}`. Must be one of {supported_orders}')


def validate_json_param(name: str, value: str) -> MutableJSON:
    try:
        return json.loads(value)
    except json.decoder.JSONDecodeError:
        raise BRE(f'The {name!r} parameter is not valid JSON')


globals().update(app.catalog_controller.handlers())

globals().update(app.repository_controller.handlers())


def _hoist_parameters(query_params, request):
    if request.method in ('POST', 'PUT'):
        body = request.json_body
        if body is not None:
            if not isinstance(body, dict):
                raise BRE('Request body is not a JSON object')
            elif body.keys() & query_params.keys():
                raise BRE('Conflicting keys between body and query parameters')
            else:
                query_params.update(body)


globals().update(app.manifest_controller.handlers())

globals().update(app.download_controller.handlers())

globals().update(app.drs_controller.handlers())
