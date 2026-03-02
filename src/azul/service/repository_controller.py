from collections.abc import (
    Mapping,
)
from functools import (
    partial,
)
import logging
from typing import (
    Any,
    cast,
)

from chalice import (
    BadRequestError,
    NotFoundError,
    Response,
)

from azul import (
    CatalogName,
    cache,
    cached_property,
    iif,
)
from azul.auth import (
    Authentication,
)
from azul.indexer.document import (
    EntityType,
)
from azul.indexer.field import (
    FieldType,
    pass_thru_bool,
)
from azul.openapi import (
    format_description as fd,
    params,
    responses,
    schema,
)
from azul.service import (
    BadArgumentException,
)
from azul.service.controller import (
    ServiceController,
)
from azul.service.elasticsearch_service import (
    IndexNotFoundError,
    Pagination,
)
from azul.service.repository_service import (
    EntityNotFoundError,
    RepositoryService,
)
from azul.types import (
    JSON,
)
from azul.uuids import (
    InvalidUUIDError,
)

log = logging.getLogger(__name__)


class RepositoryController(ServiceController):
    min_page_size = 1

    generic_object_spec = schema.object(additionalProperties=True)

    array_of_object_spec = schema.array(generic_object_spec)

    hit_spec = schema.object(
        additionalProperties=True,
        protocols=array_of_object_spec,
        entryId=str,
        sources=array_of_object_spec,
        samples=array_of_object_spec,
        specimens=array_of_object_spec,
        cellLines=array_of_object_spec,
        donorOrganisms=array_of_object_spec,
        organoids=schema.array(str),
        cellSuspensions=array_of_object_spec
    )

    page_spec = schema.object(
        hits=schema.array(hit_spec),
        pagination=generic_object_spec,
        termFacets=generic_object_spec
    )

    def repository_id_spec(self):
        search_spec_link = '#operations-Index-get_index__entity_type_'
        return {
            'summary': 'Detailed information on a particular entity.',
            'tags': ['Index'],
            'parameters': [
                self.catalog_param_spec,
                params.path('entity_type', str, description='The type of the desired entity'),
                params.path('entity_id', str, description='The UUID of the desired entity')
            ],
            'responses': {
                '200': {
                    'description': fd(f'''
                        This response describes a single entity. To search the index
                        for multiple entities, see the [corresponding search
                        endpoint]({search_spec_link}).

                        The properties that are common to all entity types are
                        listed in the schema below; however, additional properties
                        may be present for certain entity types. With the exception
                        of the entity's unique identifier, all properties are
                        arrays, even in cases where only one value is present.

                        The structures of the objects within these arrays are not
                        perfectly consistent, since they may represent either
                        singleton entities or aggregations depending on context.

                        For example, any biomaterial that yields a cell suspension
                        which yields a sequence file will be considered a "sample".
                        Therefore, the `samples` field is polymorphic, and each
                        sample may be either a specimen, an organoid, or a cell line
                        (the field `sampleEntityType` can be used to discriminate
                        between these cases).
                    '''),
                    **responses.json_content(self.hit_spec)
                }
            }
        }

    def repository_search_spec(self, *, post: bool):
        id_spec_link = '#operations-Index-get_index__entity_type___entity_id_'
        return {
            'summary': fd(f'''
                Search an index for entities of interest
                {", with filters provided in the request body" if post else ""}.
            '''),
            'deprecated': post,
            'description':
                iif(post, self.parameter_hoisting_note('GET', '/index/files', 'POST') + fd('''

                Note that the Swagger UI can't currently be used to pass a body.

                Please also note that this endpoint should be considered beta and
                may change or disappear in the future. That is the reason for the
                deprecation.
            ''')),
            'tags': ['Index'],
            'parameters': self.repository_search_params_spec(),
            'responses': {
                '200': {
                    'description': fd(f'''
                        Paginated list of entities that meet the search criteria
                        ("hits"). The structure of these hits is documented under
                        the [corresponding endpoint for a specific
                        entity]({id_spec_link}).

                        The `pagination` section describes the total number of hits
                        and total number of pages, as well as user-supplied search
                        parameters for page size and sorting behavior. It also
                        provides links for navigating forwards and backwards between
                        pages of results.

                        The `termFacets` section tabulates the occurrence of unique
                        values within nested fields of the `hits` section across all
                        entities meeting the filter criteria (this includes entities
                        not listed on the current page, meaning that this section
                        will be invariable across all pages from the same search).
                        Not every nested field is tabulated, but the set of
                        tabulated fields is consistent between entity types.
                    '''),
                    **responses.json_content(self.page_spec)
                }
            }
        }

    def repository_search_params_spec(self):
        return [
            self.catalog_param_spec,
            self.filters_param_spec,
            params.path(
                'entity_type',
                schema.enum(*self.app.metadata_plugin.exposed_indices.keys()),
                description='Which index to search.'
            ),
            params.query(
                'size',
                schema.optional(schema.default(10, form=schema.range(self.min_page_size, None))),
                description=fd('''
                    The number of hits included per page. The maximum size allowed
                    depends on the catalog and entity type.
                ''')
            ),
            params.query(
                'sort',
                schema.optional(schema.enum(*self.app.organic_fields)),
                description=fd('''
                    The field to sort the hits by. The default value depends on the
                    entity type.
                ''')
            ),
            params.query(
                'order',
                schema.optional(schema.enum('asc', 'desc')),
                description=fd('''
                    The ordering of the sorted hits, either ascending or descending.
                    The default value depends on the entity type.
                ''')
            ),
            *[
                params.query(
                    param,
                    schema.optional(str),
                    description=fd('''
                        Use the `next` and `previous` properties of the
                        `pagination` response element to navigate between pages.
                    '''),
                    deprecated=True)
                for param in [
                    'search_before',
                    'search_before_uid',
                    'search_after',
                    'search_after_uid'
                ]
            ]
        ]

    def repository_head_search_spec(self):
        return {
            **self.repository_head_spec(),
            'parameters': self.repository_search_params_spec()
        }

    def repository_head_spec(self, for_summary: bool = False):
        search_spec_link = f'#operations-Index-get_index_{"summary" if for_summary else "_entity_type_"}'
        return {
            'summary': 'Perform a query without returning its result.',
            'tags': ['Index'],
            'responses': {
                '200': {
                    'description': fd(f'''
                        The HEAD method can be used to test whether an index is
                        operational, or to check the validity of query parameters
                        for the [GET method]({search_spec_link}).
                    ''')
                }
            }
        }

    @cached_property
    def repository_summary_spec(self):
        return {
            'tags': ['Index'],
            'parameters': [self.catalog_param_spec, self.filters_param_spec]
        }

    def validate_size(self, entity_type: EntityType, size: str):
        sorting = self.app.metadata_plugin.exposed_indices[entity_type]
        try:
            size = int(size)
        except BaseException:
            raise BadRequestError('Invalid value for parameter `size`')
        else:
            if size > sorting.max_page_size:
                raise BadRequestError(f'Invalid value for parameter `size`, '
                                      f'must not be greater than {sorting.max_page_size}')
            elif size < self.min_page_size:
                raise BadRequestError('Invalid value for parameter `size`, must be greater than 0')

    def validate_order(self, order: str):
        supported_orders = ('asc', 'desc')
        if order not in supported_orders:
            raise BadRequestError(f'Unknown order `{order}`. Must be one of {supported_orders}')

    def handlers(self) -> dict[str, Any]:
        @self.app.route(
            '/index/{entity_type}',
            methods=['GET'],
            spec=self.repository_search_spec(post=False),
            cors=True
        )
        # FIXME: Properly document the POST version of /index
        #        https://github.com/DataBiosphere/azul/issues/5900
        @self.app.route(
            '/index/{entity_type}',
            methods=['POST'],
            content_types=['application/json'],
            spec=self.repository_search_spec(post=True),
            cors=True
        )
        @self.app.route(
            '/index/{entity_type}',
            methods=['HEAD'],
            spec=self.repository_head_search_spec(),
            cors=True
        )
        @self.app.route(
            '/index/{entity_type}/{entity_id}',
            methods=['GET'],
            spec=self.repository_id_spec(),
            cors=True
        )
        def repository_search(entity_type: str, entity_id: str | None = None) -> JSON:
            request = self.app.current_request
            query_params = request.query_params or {}
            self._hoist_parameters(query_params, request)
            validate_params(query_params,
                            catalog=validate_catalog,
                            filters=self.validate_filters,
                            order=self.validate_order,
                            search_after=partial(self.validate_json_param, 'search_after'),
                            search_after_uid=str,
                            search_before=partial(self.validate_json_param, 'search_before'),
                            search_before_uid=str,
                            size=partial(self.validate_size, entity_type),
                            sort=self.validate_field)
            validate_entity_type(entity_type)
            response = self.search(catalog=self.app.catalog,
                                   entity_type=entity_type,
                                   item_id=entity_id,
                                   filters=query_params.get('filters'),
                                   pagination=self.app.get_pagination(entity_type),
                                   authentication=request.authentication)
            return '' if request.method == 'HEAD' else response

        @self.app.route(
            '/index/summary',
            methods=['GET'],
            cors=True,
            spec={
                'summary': 'Statistics on the data present across all entities.',
                'responses': {
                    '200': {
                        # FIXME: Add 'projects' to API documentation & schema
                        #        https://github.com/DataBiosphere/azul/issues/3917
                        'description': fd('''
                            Counts the total number and total size in bytes of assorted
                            entities, subject to the provided filters.

                            `fileTypeSummaries` provides the count and total size in
                            bytes of files grouped by their format, e.g. "fastq" or
                            "matrix." `fileCount` and `totalFileSize` compile these
                            figures across all file formats. Likewise,
                            `cellCountSummaries` counts cells and their associated
                            documents grouped by organ type, with `organTypes` listing
                            all referenced organs.

                            Total counts of unique entities are also provided for other
                            entity types such as projects and tissue donors. These
                            values are not grouped/aggregated.
                        '''),
                        **responses.json_content(
                            schema.object(
                                additionalProperties=True,
                                organTypes=schema.array(str),
                                totalFileSize=float,
                                fileTypeSummaries=self.array_of_object_spec,
                                cellCountSummaries=self.array_of_object_spec,
                                donorCount=int,
                                fileCount=int,
                                labCount=int,
                                projectCount=int,
                                speciesCount=int,
                                specimenCount=int
                            )
                        )
                    }
                },
                **self.repository_summary_spec
            }
        )
        @self.app.route(
            '/index/summary',
            methods=['HEAD'],
            spec={
                **self.repository_head_spec(for_summary=True),
                **self.repository_summary_spec
            }
        )
        def get_summary():
            """
            Returns a summary based on the filters passed on to the call. Based on the
            ICGC endpoint.
            :return: Returns a jsonified Summary API response
            """
            request = self.app.current_request
            query_params = request.query_params or {}
            validate_params(query_params,
                            filters=str,
                            catalog=validate_catalog)
            filters = query_params.get('filters', '{}')
            self.validate_filters(filters)
            response = self.summary(catalog=self.app.catalog,
                                    filters=filters,
                                    authentication=request.authentication)
            return '' if request.method == 'HEAD' else response

        @self.app.route(
            '/repository/sources',
            methods=['GET'],
            cors=True,
            spec={
                'summary': 'List available data sources',
                'tags': ['Repository'],
                'parameters': [self.catalog_param_spec],
                'responses': {
                    '200': {
                        'description': fd('''
                            List the sources the currently authenticated user is
                            authorized to access in the underlying data repository.
                        '''),
                        **responses.json_content(
                            schema.object(sources=schema.array(
                                schema.object(
                                    sourceId=str,
                                    sourceSpec=str
                                )
                            ))
                        )
                    }
                }
            }
        )
        def list_sources() -> Response:
            validate_params(self.app.current_request.query_params or {},
                            catalog=validate_catalog)
            sources = self.list_sources(self.app.catalog,
                                        self.app.current_request.authentication)
            return Response(body={'sources': sources}, status_code=200)

        return locals()

    @cached_property
    def service(self) -> RepositoryService:
        return RepositoryService()

    def search(self,
               *,
               catalog: CatalogName,
               entity_type: str,
               item_id: str | None,
               filters: str | None,
               pagination: Pagination,
               authentication: Authentication
               ) -> JSON:
        filters = self.get_filters(catalog, authentication, filters)
        try:
            response = self.service.search(catalog=catalog,
                                           entity_type=entity_type,
                                           file_url_func=self.file_url_func,
                                           item_id=item_id,
                                           filters=filters,
                                           pagination=pagination)
        except (BadArgumentException, InvalidUUIDError) as e:
            raise BadRequestError(e)
        except (EntityNotFoundError, IndexNotFoundError) as e:
            raise NotFoundError(e)
        return cast(JSON, response)

    def summary(self,
                *,
                catalog: CatalogName,
                filters: str,
                authentication: Authentication
                ) -> JSON:
        filters = self.get_filters(catalog, authentication, filters)
        try:
            response = self.service.summary(catalog, filters)
        except BadArgumentException as e:
            raise BadRequestError(e)
        return cast(JSON, response)

    @cache
    def field_types(self, catalog: CatalogName) -> Mapping[str, FieldType]:
        """
        Returns the field type for each supported sort and filter field, using
        the name of the field as provided by clients.
        """
        result = {}
        plugin = self.service.metadata_plugin(catalog)
        for field, path in plugin.field_mapping.items():
            field_type = self.service.field_type(catalog, path)
            if isinstance(field_type, FieldType):
                result[field] = field_type
        # This field is a synthetic element of the response and will never be
        # null. Including it here helps to streamline request validation.
        accessible_field = plugin.special_fields.accessible.name
        assert accessible_field not in result, result
        result[accessible_field] = pass_thru_bool
        return result
