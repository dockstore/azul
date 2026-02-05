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
            message_cls = SQSFifoMessage if self.actions_are_fifo else SQSMessage
            message = message_cls.from_record(record)
            log.info('Worker handling message %r', message)
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
