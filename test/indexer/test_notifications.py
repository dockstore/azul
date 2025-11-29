from unittest.mock import (
    patch,
)
from uuid import (
    uuid4,
)

from moto import (
    mock_aws,
)
from requests import (
    Request,
    Response,
)

from app_test_case import (
    LocalAppTestCase,
)
from azul.deployment import (
    aws,
)
from azul.hmac import (
    SignatureHelper,
)
from azul.types import (
    JSON,
)
from azul_test_case import (
    DCP1TestCase,
)
from sqs_test_case import (
    SqsTestCase,
)


class TestIndexerApp(LocalAppTestCase, DCP1TestCase, SqsTestCase):

    @classmethod
    def app_name(cls) -> str:
        return 'indexer'

    @mock_aws
    def test_successful_notifications(self):
        self._create_mock_notifications_queue()
        body = {
            'bundle_fqid': {
                'uuid': 'bb2365b9-5a5b-436f-92e3-4fc6d86a9efd',
                'version': '2018-03-28T13:55:26.044Z'
            }
        }
        for delete in False, True:
            with self.subTest(delete=delete):
                response = self._test(body, delete=delete, valid_auth=True)
                self.assertEqual(202, response.status_code)
                self.assertEqual('', response.text)

    @mock_aws
    def test_invalid_notifications(self):
        bodies = {
            'Missing notification entry: bundle_fqid': {},
            'Missing notification entry: bundle_fqid.uuid': {
                'bundle_fqid': {
                    'version': '2018-03-28T13:55:26.044Z'
                }
            },
            "Invalid type: uuid: <class 'NoneType'> (should be str)": {
                'bundle_fqid': {
                    'uuid': None,
                    'version': '2018-03-28T13:55:26.044Z'
                }
            },
            'Missing notification entry: bundle_fqid.version': {
                'bundle_fqid': {
                    'uuid': 'bb2365b9-5a5b-436f-92e3-4fc6d86a9efd'
                }
            },
            "Invalid type: version: <class 'NoneType'> (should be str)": {
                'bundle_fqid': {
                    'uuid': 'bb2365b9-5a5b-436f-92e3-4fc6d86a9efd',
                    'version': None
                }
            },
            'Invalid syntax: }9fccaed8-cdbc-445e-a3a0-6edc11f4b73f{ (should be a UUID)': {
                'bundle_fqid': {
                    'uuid': '}9fccaed8-cdbc-445e-a3a0-6edc11f4b73f{',
                    'version': '2019-12-31T00:00:00.000Z'
                }
            },
            'Invalid syntax: bundle_version can not be empty': {
                'bundle_fqid': {
                    'uuid': str(uuid4()),
                    'version': ''
                }
            }
        }
        for delete in False, True:
            for expected_message, body in bodies.items():
                with self.subTest(delete=delete, expected_message=expected_message):
                    response = self._test(body, delete=delete, valid_auth=True)
                    expected_response = {
                        'Code': 'BadRequestError',
                        'Message': expected_message
                    }
                    self.assertEqual(400, response.status_code)
                    self.assertEqual(expected_response, response.json())

    @mock_aws
    def test_invalid_auth_for_notification_request(self):
        self._create_mock_notifications_queue()
        body = {
            'bundle_fqid': {
                'uuid': str(uuid4()),
                'version': 'SomeBundleVersion'
            }
        }
        for delete in False, True:
            with self.subTest(delete=delete):
                response = self._test(body, delete=delete, valid_auth=False)
                self.assertEqual(401, response.status_code)

    def _test(self, body: JSON, *, delete: bool, valid_auth: bool) -> Response:
        with patch.object(aws, 'get_hmac_key_and_id') as get_hmac_key_and_id:
            get_hmac_key_and_id.return_value = b'good key', 'the id'
            url = self.base_url.set(path=(self.catalog, 'bundles'))
            method = 'DELETE' if delete else 'POST'
            request = Request(method=method, url=str(url), json=body)
            hmac_support = SignatureHelper()
            if valid_auth:
                return hmac_support.sign_and_send(request)
            else:
                with patch.object(hmac_support, 'resolve_private_key') as p:
                    p.return_value = b'bad key'
                    return hmac_support.sign_and_send(request)
