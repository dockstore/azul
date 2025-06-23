import json
import logging
import time
from typing import (
    Callable,
    Iterable,
)

import chalice
from chalice.app import (
    SQSRecord,
)

from azul import (
    R,
    cached_property,
)
from azul.azulclient import (
    Action,
)
from azul.chalice import (
    AppController,
)
from azul.types import (
    JSON,
    derived_type_params,
)

log = logging.getLogger(__name__)


class ActionController[A: Action](AppController):

    @cached_property
    def _action_cls(self) -> type[A]:
        action_cls = derived_type_params(type(self), root=ActionController)[A]
        assert isinstance(action_cls, type), action_cls
        return action_cls

    def _load_action(self, action_str: str) -> A:
        action_cls = self._action_cls
        try:
            action = action_cls.from_json(action_str)
        except AssertionError as e:
            if R.caused(e):
                raise R.propagate(e, chalice.BadRequestError)
            else:
                raise
        else:
            return action

    def _handle_events(self,
                       event: Iterable[SQSRecord],
                       message_handler: Callable[[A, JSON], None]):
        for record in event:
            message = json.loads(record.body)
            attempts = record.to_dict()['attributes']['ApproximateReceiveCount']
            log.info('Worker handling message %r, attempt #%r (approx).',
                     message, attempts)
            start = time.time()
            try:
                action = self._load_action(message['action'])
                message_handler(action, message)
            except BaseException:
                # Note that another problematic outcome is for the Lambda invocation
                # to time out, in which case this log message will not be written.
                log.warning('Worker failed to handle message %r', message, exc_info=True)
                raise
            else:
                duration = time.time() - start
                log.info('Worker successfully handled message %r in %.3fs.', message, duration)
