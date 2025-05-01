import logging
from typing import (
    Iterable,
)

from chalice.app import (
    SQSRecord,
)

from azul import (
    cached_property,
)
from azul.azulclient import (
    AzulClient,
    MirrorAction,
)
from azul.indexer.action_controller import (
    ActionController,
)
from azul.indexer.mirror_service import (
    MirrorService,
)
from azul.types import (
    JSON,
    json_str,
)

log = logging.getLogger(__name__)


class MirrorController(ActionController[MirrorAction]):

    @cached_property
    def client(self) -> AzulClient:
        return AzulClient()

    @property
    def service(self) -> MirrorService:
        return self.client.mirror_service

    def mirror(self, event: Iterable[SQSRecord]):
        self._handle_events(event, self._mirror)

    def _mirror(self, message: JSON):
        action = self._load_action(json_str(message['action']))
        if action is MirrorAction.mirror_source:
            self.client.mirror_source(message['catalog'], message['source'])
        elif action is MirrorAction.mirror_partition:
            self.client.mirror_partition(message['catalog'],
                                         message['source'],
                                         message['prefix'])
        elif action is MirrorAction.mirror_file:
            self.client.mirror_file(message['catalog'],
                                    message['file'])
        elif action is MirrorAction.mirror_part:
            self.client.mirror_file_part(message['catalog'],
                                         message['file'],
                                         message['part'],
                                         message['upload_id'],
                                         message['etags'])
        elif action is MirrorAction.finalize_file:
            self.client.finalize_file(message['catalog'],
                                      message['file'],
                                      message['upload_id'],
                                      message['etags'])
        else:
            assert False, action
