import json

from chalice.app import (
    SQSRecord,
)

from azul import (
    cached_property,
    config,
)
from azul.azulclient import (
    AzulClient,
)
from azul.deployment import (
    aws,
)
from azul.queues import (
    Queues,
)
from azul.types import (
    MutableJSONs,
)
from azul_test_case import (
    AzulUnitTestCase,
)


class SqsTestCase(AzulUnitTestCase):

    def _create_mock_queues(self, queue_names: list[str]) -> None:
        if queue_names:
            self.assertIsSubset(set(queue_names), set(config.all_queue_names))
        else:
            queue_names = config.all_queue_names

        sqs = aws.sqs_resource
        for queue_name in queue_names:
            sqs.create_queue(QueueName=queue_name,
                             Attributes=dict(FifoQueue='true') if queue_name.endswith('.fifo') else {})

    def _create_mock_notifications_queue(self):
        self._create_mock_queues([config.notifications_queue.name])


class WorkQueueTestCase(SqsTestCase):

    @cached_property
    def queues(self) -> Queues:
        return Queues(delete=True)

    @cached_property
    def client(self) -> AzulClient:
        return AzulClient()

    def _read_queue(self, queue) -> MutableJSONs:
        messages = self.queues.read_messages(queue)
        # For unknown reasons, Moto 4.0.6 requires reading the queues a second
        # time whereas 2.0.6 didn't. It *is* more realistic, but I am not sure
        # how reliable this is.
        messages += self.queues.read_messages(queue)
        message_bodies = [json.loads(m.body) for m in messages]
        return message_bodies

    def _mock_sqs_record(self, body, *, attempts: int = 1):
        event_dict = {
            'body': json.dumps(body),
            'receiptHandle': 'ThisWasARandomString',
            'attributes': {'ApproximateReceiveCount': attempts}
        }
        return SQSRecord(event_dict=event_dict, context={})
