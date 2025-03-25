import json
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

log = logging.getLogger(__name__)


class MirrorController(ActionController[MirrorAction]):

    @cached_property
    def client(self) -> AzulClient:
        return AzulClient()

    def mirror(self, event: Iterable[SQSRecord]):
        for record in event:
            message = json.loads(record.body)
            log.info('Worker handling message %r', message)
            start = time.time()
            try:
                action = self._load_action(message['action'])
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
            except BaseException:
                log.warning(f'Worker failed to handle message {message}.', exc_info=True)
                raise
            else:
                duration = time.time() - start
                log.info(f'Worker successfully handled message {message} in {duration:.3f}s.')
