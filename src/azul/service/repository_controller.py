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
)
from azul.auth import (
    Authentication,
)
from azul.indexer.field import (
    FieldType,
    pass_thru_bool,
)
from azul.openapi import (
    format_description as fd,
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

    def handlers(self) -> dict[str, Any]:
        @self.app.route(
            '/index/{entity_type}',
            methods=['GET'],
            spec=repository_search_spec(post=False),
            cors=True
        )
        # FIXME: Properly document the POST version of /index
        #        https://github.com/DataBiosphere/azul/issues/5900
        @self.app.route(
            '/index/{entity_type}',
            methods=['POST'],
            content_types=['application/json'],
            spec=repository_search_spec(post=True),
            cors=True
        )
        @self.app.route(
            '/index/{entity_type}',
            methods=['HEAD'],
            spec=repository_head_search_spec(),
            cors=True
        )
        @self.app.route(
            '/index/{entity_type}/{entity_id}',
            methods=['GET'],
            spec=repository_id_spec(),
            cors=True
        )
        def repository_search(entity_type: str, entity_id: str | None = None) -> JSON:
            request = self.app.current_request
            query_params = request.query_params or {}
            _hoist_parameters(query_params, request)
            validate_params(query_params,
                            catalog=validate_catalog,
                            filters=validate_filters,
                            order=validate_order,
                            search_after=partial(validate_json_param, 'search_after'),
                            search_after_uid=str,
                            search_before=partial(validate_json_param, 'search_before'),
                            search_before_uid=str,
                            size=partial(validate_size, entity_type),
                            sort=validate_field)
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
                                fileTypeSummaries=array_of_object_spec,
                                cellCountSummaries=array_of_object_spec,
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
                **repository_summary_spec
            }
        )
        @self.app.route(
            '/index/summary',
            methods=['HEAD'],
            spec={
                **repository_head_spec(for_summary=True),
                **repository_summary_spec
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
            validate_filters(filters)
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
                'parameters': [catalog_param_spec],
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
