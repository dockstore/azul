from unittest.mock import (
    MagicMock,
    PropertyMock,
    patch,
)

from more_itertools import (
    one,
)
from moto import (
    mock_aws,
)

from azul import (
    config,
)
from azul.indexer.mirror_controller import (
    MirrorController,
)
from azul.logging import (
    configure_test_logging,
    get_test_logger,
)
from azul_test_case import (
    DCP2TestCase,
)
from sqs_test_case import (
    WorkQueueTestCase,
)

log = get_test_logger(__name__)


# noinspection PyPep8Naming
def setUpModule():
    configure_test_logging(log)


@mock_aws
class TestMirrorController(DCP2TestCase, WorkQueueTestCase):

    @classmethod
    def _patch_enable_mirroring(cls):
        cls.addClassPatch(patch.object(type(config),
                                       'enable_mirroring',
                                       new=PropertyMock(return_value=True)))

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._patch_enable_mirroring()

    def setUp(self):
        super().setUp()
        self._create_mock_queues(config.mirror_queue.name,
                                 config.mirror_queue.to_fail.name)

    def test_mirroring(self):
        with self.subTest('remote_mirror'):
            self.client.remote_mirror(self.catalog, [self.source])
            source_message = one(self._read_queue(self.client.mirror_queue()))
            expected_message = dict(action='mirror_source',
                                    catalog=self.catalog,
                                    source=self.source.to_json())
            self.assertEqual(expected_message, source_message)

        with self.subTest('mirror_source'):
            event = [self._mock_sqs_record(source_message)]
            controller = MirrorController(app=MagicMock())
            controller.mirror(event)
            partition_messages = self._read_queue(self.client.mirror_queue())
            partitions = []
            for message in partition_messages:
                partitions.append(message.pop('prefix'))
                self.assertEqual(dict(action='mirror_partition',
                                      catalog=self.catalog,
                                      source=self.source.to_json()),
                                 message)
            self.assertEqual(list(self.source.spec.prefix.partition_prefixes()), partitions)
