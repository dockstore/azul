import logging

from azul.plugins import (
    MetadataPlugin,
)
from azul.service.controller import (
    ServiceController,
)
from azul.service.elasticsearch_service import (
    ElasticsearchService,
)

log = logging.getLogger(__name__)


class QueryController(ServiceController):
    service: ElasticsearchService

    @property
    def _metadata_plugin(self) -> MetadataPlugin:
        return self.service.metadata_plugin(self.app.catalog)
