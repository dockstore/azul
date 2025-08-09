from abc import (
    ABCMeta,
    abstractmethod,
)
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
from azul.chalice import (
    AppController,
)
from azul.queues import (
    Action,
    SQSFifoMessage,
    SQSMessage,
)
from azul.types import (
    JSON,
    derived_type_params,
    json_str,
)

log = logging.getLogger(__name__)


class ActionController[A: Action](AppController, metaclass=ABCMeta):

    @property
    @abstractmethod
    def actions_are_fifo(self) -> bool:
        raise NotImplementedError

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
            message: SQSMessage
            if self.actions_are_fifo:
                message = SQSFifoMessage.from_record(record)
                suffix, args = ', group ID %s', [message.group_id]
            else:
                message = SQSMessage.from_record(record)
                suffix, args = '', []
            log.info('Worker handling message %r, ' +
                     'attempt #%i (approx), message ID %s' + suffix,
                     message.body, message.attempts, message.id, *args)
            start = time.time()
            try:
                action = self._load_action(json_str(message.body['action']))
                message_handler(action, message.body)
            except BaseException:
                # Note that another problematic outcome is for the Lambda invocation
                # to time out, in which case this log message will not be written.
                log.warning('Worker failed to handle message %r', message, exc_info=True)
                raise
            else:
                duration = time.time() - start
                log.info('Worker successfully handled message %r in %.3fs.', message, duration)
