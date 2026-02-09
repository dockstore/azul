import json
import time
from typing import (
    Mapping,
)
from unittest import (
    mock,
)

from moto import (
    mock_aws,
)
from mypy_boto3_dynamodb.literals import (
    ScalarAttributeTypeType,
)

from azul import (
    NotInLambdaContextException,
)
from azul.service.source_service import (
    Expired,
    NotFound,
    SourceService,
)
from azul_test_case import (
    DCP2TestCase,
)
from dynamodb_test_case import (
    DynamoDBTestCase,
)


@mock_aws
class TestSourceCache(DynamoDBTestCase):

    def _dynamodb_table_name(self) -> str:
        return SourceService.table_name

    def _dynamodb_attributes(self) -> Mapping[str, ScalarAttributeTypeType]:
        return {SourceService.key_attribute: 'S'}

    def _dynamodb_hash_key(self) -> str:
        return SourceService.key_attribute

    wait = 2

    @mock.patch.object(SourceService, attribute='expiration', new=wait)
    def test_source_cache(self):
        key = 'foo'
        value = [{'bar': 'baz'}]
        service = SourceService()
        with self.assertRaises(NotFound):
            service._get('nil')
        service._put(key, value)
        self.assertEqual(service._get(key), value)
        time.sleep(self.wait + 1)
        with self.assertRaises(Expired):
            service._get(key)


class TestConfiguredSources(DCP2TestCase):

    @classmethod
    def _patch_configured_sources(cls):
        pass  # don't call super so that code under test isn't patched

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()

        class MockPlugin:

            def list_sources(self, authentication):
                assert authentication is None, authentication
                return [cls.source]

        cls.addClassPatch(mock.patch.object(SourceService,
                                            'repository_plugin',
                                            return_value=MockPlugin()))

    def test(self):
        actuals, outsourced = [], []

        def test():
            service = SourceService()
            actuals.append(service.configured_public_sources)
            outsourced.append(service.configured_public_sources_for_outsourcing)
            mock_open_resource.assert_called_once()

        target = SourceService.__module__ + '.open_resource'

        with mock.patch(target,
                        side_effect=NotInLambdaContextException('')
                        ) as mock_open_resource:
            test()

        with mock.patch(target,
                        new_callable=mock.mock_open,
                        read_data=json.dumps(outsourced[0])
                        ) as mock_open_resource:
            test()

        self.assertEqual(*outsourced)
        self.assertEqual([{self.catalog: [self.source]}] * 2, actuals)
