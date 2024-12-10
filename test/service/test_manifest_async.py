from contextlib import (
    contextmanager,
)
import datetime
from itertools import (
    product,
)
import json
from typing import (
    ContextManager,
)
from unittest import (
    mock,
)
from unittest.mock import (
    patch,
)
from uuid import (
    UUID,
)

from botocore.exceptions import (
    ClientError,
)
from furl import (
    furl,
)
from moto import (
    mock_aws,
)
import requests

from app_test_case import (
    LocalAppTestCase,
)
from azul.logging import (
    configure_test_logging,
)
from azul.plugins import (
    ManifestFormat,
)
from azul.service import (
    Filters,
)
from azul.service.async_manifest_service import (
    AsyncManifestService,
    GenerationFailed,
    InvalidTokenError,
    Token,
)
from azul.service.manifest_controller import (
    ManifestGenerationState,
)
from azul.service.manifest_service import (
    BareManifestKey,
    CachedManifestNotFound,
    Manifest,
    ManifestKey,
    ManifestPartition,
    ManifestService,
    SignedManifestKey,
)
from azul_test_case import (
    AzulUnitTestCase,
    DCP1TestCase,
)


# noinspection PyPep8Naming
def setUpModule():
    configure_test_logging()


@patch.object(AsyncManifestService, '_sfn')
class TestAsyncManifestService(AzulUnitTestCase):
    execution_id = UUID('1ea94a54-a64d-54f1-8b41-15455fb958db')

    def test_token_encoding(self, _sfn):
        token = Token(execution_id=self.execution_id, request_index=42, retry_after=123)
        self.assertEqual(token, Token.decode(token.encode()))

    def test_token_validation(self, _sfn):
        token = Token(execution_id=self.execution_id, request_index=42, retry_after=123)
        self.assertRaises(InvalidTokenError, token.decode, token.encode()[:-1])

    def test_status_success(self, _sfn):
        """
        A successful manifest job should return a 302 status and a URL to the
        manifest
        """
        service = AsyncManifestService()
        execution_name = service.execution_name(self.execution_id)
        output = {'foo': 'bar'}
        _sfn.describe_execution.return_value = {
            'executionArn': service.execution_arn(execution_name),
            'stateMachineArn': service.machine_arn,
            'name': execution_name,
            'status': 'SUCCEEDED',
            'startDate': datetime.datetime(2018, 11, 15, 18, 30, 44, 896000),
            'stopDate': datetime.datetime(2018, 11, 15, 18, 30, 59, 295000),
            'input': '{"filters": {}}',
            'output': json.dumps(output)
        }
        token = Token(execution_id=self.execution_id, request_index=0, retry_after=0)
        actual_output = service.inspect_generation(token)
        self.assertEqual(output, actual_output)

    def test_status_running(self, _sfn):
        """
        A running manifest job should return a 301 status and a URL to retry
        checking the job status
        """
        service = AsyncManifestService()
        execution_name = service.execution_name(self.execution_id)
        _sfn.describe_execution.return_value = {
            'executionArn': service.execution_arn(execution_name),
            'stateMachineArn': service.machine_arn,
            'name': execution_name,
            'status': 'RUNNING',
            'startDate': datetime.datetime(2018, 11, 15, 18, 30, 44, 896000),
            'input': '{"filters": {}}'
        }
        token = Token(execution_id=self.execution_id, request_index=0, retry_after=0)
        token = service.inspect_generation(token)
        expected = Token(execution_id=self.execution_id, request_index=1, retry_after=1)
        self.assertEqual(expected, token)

    def test_status_failed(self, _sfn):
        """
        A failed manifest job should raise a GenerationFailed
        """
        service = AsyncManifestService()
        execution_name = service.execution_name(self.execution_id)
        _sfn.describe_execution.return_value = {
            'executionArn': service.execution_arn(execution_name),
            'stateMachineArn': service.machine_arn,
            'name': execution_name,
            'status': 'FAILED',
            'startDate': datetime.datetime(2018, 11, 14, 16, 6, 53, 382000),
            'stopDate': datetime.datetime(2018, 11, 14, 16, 6, 55, 860000),
            'input': '{"filters": {"organ": {"is": ["lymph node"]}}}',
        }
        token = Token(execution_id=self.execution_id, request_index=0, retry_after=0)
        with self.assertRaises(GenerationFailed):
            service.inspect_generation(token)


class TestManifestController(DCP1TestCase, LocalAppTestCase):

    @classmethod
    def lambda_name(cls) -> str:
        return 'service'

    execution_id = UUID('1ea94a54-a64d-54f1-8b41-15455fb958db')

    @mock_aws
    @mock.patch.object(AsyncManifestService, '_sfn')
    @mock.patch.object(ManifestService, 'get_manifest')
    @mock.patch.object(ManifestService, 'get_cached_manifest')
    @mock.patch.object(ManifestService, 'verify_manifest_key')
    @mock.patch.object(ManifestService, 'get_cached_manifest_with_key')
    @mock.patch.object(ManifestService, 'get_manifest_url')
    @mock.patch.object(ManifestService, 'sign_manifest_key')
    def test(self,
             sign_manifest_key,
             get_manifest_url,
             get_cached_manifest_with_key,
             verify_manifest_key,
             get_cached_manifest,
             get_manifest,
             _sfn):
        for format, fetch in product([ManifestFormat.compact, ManifestFormat.curl],
                                     [True, False]):
            for v in locals().values():
                if isinstance(v, mock.Mock):
                    v.reset_mock(return_value=True, side_effect=True)
            with self.subTest(format=format, fetch=fetch):
                filters = {'organ': {'is': ['lymph node']}, 'fileFormat': {'is': ['txt']}}
                filters = Filters(explicit=filters, source_ids={self.source.id})
                params = {
                    'catalog': self.catalog,
                    'format': format.value,
                    'filters': json.dumps(filters.explicit)
                }
                path = ['manifest', 'files']

                initial_url = self.base_url.set(path=path.copy(), args=params)
                if fetch:
                    initial_url.path.segments.insert(0, 'fetch')

                manifest_key = ManifestKey(catalog=self.catalog,
                                           format=format,
                                           manifest_hash=UUID('d2b0ce3c-46f0-57fe-b9d4-2e38d8934fd4'),
                                           source_hash=UUID('77936747-5968-588e-809f-af842d6be9e0'))
                signed_manifest_key = SignedManifestKey(
                    value=BareManifestKey.unpack(manifest_key.pack()),
                    signature=b'123'
                )

                object_url = furl('https://url.to.manifest?foo=bar')
                file_name = 'some_file_name'
                manifest = Manifest(object_key='key/of/manifest',
                                    was_cached=False,
                                    format=format,
                                    manifest_key=manifest_key,
                                    file_name=file_name)

                partitions = [
                    ManifestPartition(index=0,
                                      is_last=False,
                                      file_name=None,
                                      config=None,
                                      multipart_upload_id=None,
                                      part_etags=None,
                                      page_index=None,
                                      is_last_page=None,
                                      search_after=None),
                    ManifestPartition(index=1,
                                      is_last=False,
                                      file_name=file_name,
                                      config=[[['foo', 'bar'], {'baz': 'blah'}]],
                                      multipart_upload_id='some_upload_id',
                                      part_etags=('some_etag',),
                                      page_index=512,
                                      is_last_page=False,
                                      search_after=('foo', 'doc#bar'))
                ]
                input: ManifestGenerationState
                input = dict(filters=filters.to_json(),
                             manifest_key=manifest_key.to_json(),
                             partition=partitions[0].to_json())
                service: AsyncManifestService
                service = self.app_module.app.manifest_controller.async_service
                execution_id = manifest_key.uuid
                execution_name = service.execution_name(execution_id)
                machine_arn = service.machine_arn
                execution_arn = service.execution_arn(execution_name)
                _sfn.start_execution.return_value = {
                    'executionArn': execution_arn,
                    'startDate': 123
                }

                def assert_get_cached_manifest(filters=filters):
                    get_cached_manifest.assert_called_once_with(
                        format=format,
                        catalog=self.catalog,
                        filters=filters
                    )
                    get_cached_manifest.reset_mock()

                def assert_get_manifest(partition):
                    get_manifest.assert_called_once_with(
                        format=format,
                        catalog=self.catalog,
                        filters=filters,
                        partition=partitions[partition],
                        manifest_key=manifest_key
                    )
                    get_manifest.reset_mock()

                # Request the manifest. The cached manifest does not exist
                # so we expect a StepFunction execution to be started and a
                # 301 redirect to the manifest endpoint with a token
                # embedded in the URL.
                #
                get_cached_manifest.side_effect = CachedManifestNotFound(manifest_key)
                url = self._request('PUT', initial_url, expect=301)
                assert_get_cached_manifest()
                token_url = url
                state: ManifestGenerationState = input
                _sfn.start_execution.assert_called_once_with(
                    stateMachineArn=machine_arn,
                    name=execution_name,
                    input=json.dumps(input)
                )
                _sfn.describe_execution.assert_not_called()
                _sfn.reset_mock()

                # Follow the redirect. We expect a call to determine the
                # status of the execution, which we mock to be still
                # running, and another 301 redirect.
                #
                _sfn.describe_execution.return_value = {'status': 'RUNNING'}
                url = self._request('GET', url, expect=301)
                get_manifest.return_value = partitions[1]
                state = self.app_module.generate_manifest(state, None)
                self.assertEqual(partitions[1],
                                 ManifestPartition.from_json(state['partition']))
                assert_get_manifest(partition=0)
                _sfn.start_execution.assert_not_called()
                _sfn.describe_execution.assert_called_once()
                _sfn.reset_mock()

                # Follow the redirect. The StepFunction has finished but the
                # output is not yet available due to eventual consistency.
                # We observed this behaviour a few years ago, but it
                # probably doesn't happen anymore. The output is most likely
                # stored on S3 under the hood which strongly consistent a
                # while back.
                #
                _sfn.describe_execution.return_value = {'status': 'SUCCEEDED'}
                url = self._request('GET', url, expect=301)
                get_manifest.return_value = manifest
                get_manifest_url.return_value = str(object_url)
                _sfn.start_execution.assert_not_called()
                _sfn.describe_execution.assert_called_once()
                _sfn.reset_mock()

                # The StepFunction has finished and the output is available.
                # We expect a 302 redirect to either the signed URL of the
                # manifest object in S3, or, when fetching a curl manifest,
                # a 302 redirect to the non-fetch endpoint with the key of
                # the manifest in the URL.
                #
                state = self.app_module.generate_manifest(state, None)
                _sfn.describe_execution.return_value = {
                    'status': 'SUCCEEDED',
                    'input': json.dumps(input),
                    'output': json.dumps(state)
                }
                if fetch and format is ManifestFormat.curl:
                    key_url = self.base_url.set(path=[*path, signed_manifest_key.encode()])
                    expected_url = key_url
                    sign_manifest_key.return_value = signed_manifest_key
                else:
                    key_url = None
                    expected_url = object_url
                url = self._request('GET', url, expect=302)
                self.assertEqual(expected_url, url)
                assert_get_manifest(partition=1)
                get_manifest.reset_mock()
                _sfn.start_execution.assert_not_called()
                _sfn.describe_execution.assert_called_once()
                _sfn.reset_mock()

                # Re-request the manifest at the initial URL. The manifest
                # is cached so we expect no intermediate 301 redirects.
                #
                get_cached_manifest.side_effect = None
                get_cached_manifest.return_value = manifest
                url = self._request('PUT', initial_url, expect=302)
                assert_get_cached_manifest()
                self.assertEqual(expected_url, url)

                # Re-request the manifest at a URL with an insignificant
                # change to the filters parameter. The cached manifest
                # should be reused. Note that this does not cover the
                # insensitivity of the manifest key derivation to such
                # insignificant differences because we mock the manifest
                # service method where that is done. However, this test is
                # not supposed to cover the service, only the controller.
                #
                equivalent_url = initial_url.copy()
                equivalent_filters = json.loads(equivalent_url.args['filters'])
                equivalent_filters = dict(reversed(equivalent_filters.items()))
                equivalent_url.args['filters'] = json.dumps(equivalent_filters)
                url = self._request('PUT', equivalent_url, expect=302)
                self.assertEqual(expected_url, url)
                assert_get_cached_manifest(filters.update(equivalent_filters))

                # Expire the cached manifest and repeat the initial request
                # with the insignificant difference. The repeated request
                # should be considered valid and matching the completed step
                # function execution.
                #
                get_cached_manifest.side_effect = CachedManifestNotFound(manifest_key)
                exception = self._mock_sfn_exception(_sfn,
                                                     operation_name='StartExecution',
                                                     error_code='ExecutionAlreadyExists')
                _sfn.start_execution.side_effect = exception
                url = self._request('PUT', equivalent_url, expect=301)
                _sfn.reset_mock(side_effect=True)
                assert_get_cached_manifest()
                # FIXME: 404 from S3 when re-requesting manifest after it expired
                #        https://github.com/DataBiosphere/azul/issues/6441
                if True:
                    self.assertEqual(token_url, url)
                else:
                    self.assertNotEqual(token_url, url)

                # Request the manifest by its key if a URL with that key
                # that was the result of the final 302 redirect above. Then
                # expire the manifest and request it again.
                #
                if key_url is not None:
                    assert signed_manifest_key.encode() == key_url.path.segments[-1]
                    verify_manifest_key.assert_not_called()
                    verify_manifest_key.return_value = manifest_key
                    get_cached_manifest_with_key.assert_not_called()
                    get_cached_manifest_with_key.return_value = manifest
                    url = self._request('GET', key_url, expect=302)
                    self.assertEqual(object_url, url)
                    verify_manifest_key.assert_called_once_with(signed_manifest_key)
                    get_cached_manifest.assert_not_called()
                    get_cached_manifest_with_key.assert_called_once_with(manifest_key)
                    get_cached_manifest_with_key.reset_mock(return_value=True)
                    get_cached_manifest_with_key.side_effect = CachedManifestNotFound(manifest_key)
                    response = requests.get(str(key_url), allow_redirects=False)
                    self.assertEqual(410, response.status_code)
                    expected_response = {
                        'Code': 'GoneError',
                        'Message': 'The requested manifest has expired, please request a new one'
                    }
                    self.assertEqual(expected_response, response.json())
                    get_cached_manifest.assert_not_called()
                    get_cached_manifest_with_key.assert_called_once_with(manifest_key)
                    get_cached_manifest_with_key.reset_mock()

    def _request(self, method: str, url: furl, *, expect: int) -> furl:
        response = requests.request(method=method,
                                    url=str(url),
                                    allow_redirects=False)
        if url.path.segments[0] == 'fetch':
            self.assertEqual(200, response.status_code)
            response = response.json()
            self.assertEqual(expect, response.pop('Status'))
            headers = response
        else:
            self.assertEqual(expect, response.status_code)
            headers = response.headers
        if expect == 301:
            self.assertGreaterEqual(int(headers['Retry-After']), 0)
        return furl(headers['Location'])

    token = Token.first(execution_id).encode()

    def _test(self, *, expected_status, token=token):
        url = self.base_url.set(path=['fetch', 'manifest', 'files', token])
        response = requests.get(str(url))
        self.assertEqual(expected_status, response.status_code)

    @contextmanager
    def _mock_error(self, error_code: str) -> ContextManager:
        with patch.object(AsyncManifestService, '_sfn') as _sfn:
            exception = self._mock_sfn_exception(_sfn,
                                                 operation_name='DescribeExecution',
                                                 error_code=error_code)
            _sfn.describe_execution.side_effect = exception
            yield

    def _mock_sfn_exception(self,
                            _sfn: mock.MagicMock,
                            operation_name: str,
                            error_code: str
                            ) -> Exception:
        exception_cls = type(error_code, (ClientError,), {})
        setattr(_sfn.exceptions, error_code, exception_cls)
        error_response = {
            'Error': {
                'Code': error_code
            }
        }
        exception = exception_cls(operation_name=operation_name,
                                  error_response=error_response)
        return exception

    def test_execution_not_found(self):
        """
        Manifest status check should raise a BadRequestError (400 status code)
        if execution cannot be found.
        """
        with self._mock_error('ExecutionDoesNotExist'):
            self._test(expected_status=400)

    def test_boto_error(self):
        """
        Manifest status check should reraise any ClientError that is not caused
        by ExecutionDoesNotExist
        """
        with self._mock_error('ServiceQuotaExceededException'):
            self._test(expected_status=500)

    def test_execution_error(self):
        """
        Manifest status check should return a generic error (500 status code)
        if the execution errored.
        """
        with patch.object(AsyncManifestService,
                          'inspect_generation',
                          side_effect=GenerationFailed):
            self._test(expected_status=500)

    def test_invalid_token(self):
        """
        Manifest endpoint should raise a BadRequestError when given a token that
        cannot be decoded
        """
        self._test(token='Invalid base64', expected_status=400)
