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

from chalice.app import (
    SQSRecord,
)

from azul.chalice import (
    AppController,
)
from azul.queues import (
    Action,
    SQSFifoMessage,
    SQSMessage,
)

log = logging.getLogger(__name__)


class ActionController[A: Action](AppController, metaclass=ABCMeta):

    @property
    @abstractmethod
    def actions_are_fifo(self) -> bool:
        raise NotImplementedError

    @property
    @abstractmethod
    def action_cls(self) -> type[A]:
        raise NotImplementedError

    def _handle_events(self,
                       event: Iterable[SQSRecord],
                       message_handler: Callable[[A], None]):
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
                action = self.action_cls.from_json(message.body)
                message_handler(action)
            except BaseException:
                # Note that another problematic outcome is for the Lambda invocation
                # to time out, in which case this log message will not be written.
                log.warning('Worker failed to handle message %r', message, exc_info=True)
                raise
            else:
                duration = time.time() - start
                log.info('Worker successfully handled message %r in %.3fs.', message, duration)
