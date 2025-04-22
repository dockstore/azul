import logging
import time
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
from azul.types import (
    JSON,
    json_str,
)

log = logging.getLogger(__name__)


class MirrorController(ActionController[MirrorAction]):

    @cached_property
    def client(self) -> AzulClient:
        return AzulClient()

    def mirror(self, event: Iterable[SQSRecord]):
        self._handle_events(event, self._mirror)

    def _mirror(self, message: JSON):
        action = self._load_action(json_str(message['action']))
        if action is MirrorAction.mirror_source:
            self.client.mirror_source(message['catalog'], message['source'])
        elif action is MirrorAction.mirror_partition:
            # FIXME: Implement mirror_partition
            #        https://github.com/DataBiosphere/azul/issues/6861
            log.info('Would mirror files in partition %r of source %r',
                     message['prefix'], message['source'])
            time.sleep(10)
        else:
            # FIXME: Implement mirror_file, mirror_part & finalize_file
            #        https://github.com/DataBiosphere/azul/issues/6862
            assert False, action
