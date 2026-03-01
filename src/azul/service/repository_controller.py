from collections.abc import (
    Mapping,
)
import logging
from typing import (
    cast,
)

from chalice import (
    BadRequestError,
    NotFoundError,
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
from azul.service import (
    BadArgumentException,
)
from azul.service.app_controller import (
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
