from functools import (
    partial,
)
import logging
from typing import (
    Any,
    Iterable,
)

import chalice
from chalice.app import (
    SQSRecord,
)

from azul import (
    R,
    cached_property,
    config,
)
from azul.chalice import (
    LambdaMetric,
)
from azul.indexer.action_controller import (
    ActionController,
)
from azul.indexer.mirror_service import (
    MirrorAction,
    MirrorService,
)
from azul.schemas import (
    SchemaController,
)

log = logging.getLogger(__name__)


class MirrorController(ActionController[MirrorAction], SchemaController):

    @property
    def actions_are_fifo(self) -> bool:
        return True

    @property
    def action_cls(self) -> type[MirrorAction]:
        return MirrorAction

    @cached_property
    def service(self) -> MirrorService:
        schema_url_func = partial(self.schema_url, facility='mirror')
        return MirrorService(schema_url_func=schema_url_func)

    def handlers(self) -> dict[str, Any]:
        if config.enable_mirroring:
            @self.app.metric_alarm(metric=LambdaMetric.errors,
                                   threshold=int(config.mirroring_concurrency * 2 / 3),
                                   period=5 * 60)
            @self.app.metric_alarm(metric=LambdaMetric.throttles,
                                   threshold=int(96000 / config.mirroring_concurrency),
                                   period=5 * 60)
            @self.app.on_sqs_message(queue=config.mirror_queue.name,
                                     batch_size=1)
            def mirror(event: chalice.app.SQSEvent):
                self.mirror(event)

        return super().handlers() | locals()

    def mirror(self, event: Iterable[SQSRecord]):
        assert config.enable_mirroring, R('Mirroring is disabled')
        self._handle_events(event, self.service.mirror)
