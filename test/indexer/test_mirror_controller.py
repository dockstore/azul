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
from azul.json import (
    copy_json,
)
from azul.logging import (
    configure_test_logging,
    get_test_logger,
)
from azul.plugins.metadata.hca import (
    HCAFile,
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
            partition_message = copy_json(partition_messages[0])
            partitions = []
            for message in partition_messages:
                partitions.append(message.pop('prefix'))
                self.assertEqual(dict(action='mirror_partition',
                                      catalog=self.catalog,
                                      source=self.source.to_json()),
                                 message)
            self.assertEqual(list(self.source.spec.prefix.partition_prefixes()), partitions)

        with self.subTest('mirror_partition'):
            event = [self._mock_sqs_record(partition_message)]
            file = HCAFile(uuid='405852c9-a0cc-4cd8-b9ff-7c6296223661',
                           name='foo.txt',
                           version=None,
                           drs_uri=None,
                           size=0,
                           content_type='text/plain',
                           sha256='123')
            plugin_cls = type(self.client.repository_plugin(self.catalog))
            with patch.object(plugin_cls, 'list_files', return_value=[file]):
                controller.mirror(event)
            file_message = one(self._read_queue(self.client.mirror_queue()))
            expected_message = dict(action='mirror_file',
                                    catalog=self.catalog,
                                    source=self.source.to_json(),
                                    file=file.to_json())
            self.assertEqual(expected_message, file_message)
