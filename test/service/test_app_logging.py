from itertools import (
    product,
)
import json
from logging import (
    DEBUG,
    INFO,
)
from unittest.mock import (
    PropertyMock,
    patch,
)

import requests

from azul import (
    Config,
)
from azul.chalice import (
    AzulChaliceApp,
    log,
)
from azul.logging import (
    configure_test_logging,
)
from indexer import (
    DCP1CannedBundleTestCase,
)
from service import (
    WebServiceTestCase,
)


# noinspection PyPep8Naming
def setUpModule():
    configure_test_logging()


class TestServiceAppLogging(DCP1CannedBundleTestCase, WebServiceTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_indices()

    @classmethod
    def tearDownClass(cls):
        cls._teardown_indices()
        super().tearDownClass()

    @classmethod
    def lambda_name(cls) -> str:
        return 'service'

    def test_request_logs(self):
        for debug, authenticated, request_body in product(
            [0, 1, 2],
            [False, True],
            [None, {'filters': json.dumps({'organ': {'is': ['foo']}})}]
        ):
            with self.subTest(azul_debug=debug,
                              authenticated=authenticated,
                              request_body=bool(request_body)):
                url = self.base_url.set(path='/index/projects')
                request_headers = {'authorization': 'Bearer foo_token'} if authenticated else {}
                level = [INFO, DEBUG, DEBUG][debug]
                with self.assertLogs(logger=log, level=level) as logs:
                    with patch.object(Config, 'debug', new=PropertyMock(return_value=debug)):
                        if request_body is not None:
                            request_headers = {
                                'content-length': str(len(json.dumps(request_body))),
                                'content-type': 'application/json',
                                **request_headers
                            }
                        response = requests.get(str(url), headers=request_headers, json=request_body)
                logs = [(r.levelno, r.getMessage()) for r in logs.records]
                body_log_level, body_log_message = logs.pop()  # asserted separately
                request_headers = {
                    'host': url.netloc,
                    'user-agent': 'python-requests/2.32.4',
                    'accept-encoding': 'gzip, deflate',
                    'accept': '*/*',
                    'connection': 'keep-alive',
                    **request_headers,
                }
                response_headers = {
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Headers': 'Authorization,'
                                                    'Content-Type,'
                                                    'X-Amz-Date,'
                                                    'X-Amz-Security-Token,'
                                                    'X-Api-Key',
                    **AzulChaliceApp.security_headers(),
                    'Cache-Control': 'no-store'
                }
                self.assertEqual(
                    [
                        (
                            INFO,
                            "Received GET request for '/index/projects', "
                            f'with {json.dumps(dict(query=None, headers=request_headers))}.'
                        ),
                        (
                            INFO,
                            '… without request body'
                        )
                        if not request_body else
                        (
                            INFO,
                            "… with request body of type (<class 'dict'>)"
                        )
                        if debug == 0 else
                        (
                            INFO,
                            f'… with a request body starting in {json.dumps(request_body)!r}'
                            if debug == 1 else
                            f'… with the 47 byte long request body {json.dumps(request_body)!r}'
                        ),
                        (
                            INFO,
                            "Authenticated request as OAuth2(access_token='foo_token')"
                            if authenticated else
                            'Did not authenticate request.'
                        ),
                        (
                            INFO,
                            'Returning 200 response with headers ' +
                            json.dumps(dict(headers=response_headers)) + '.'
                        )
                    ],
                    logs
                )
                prefix_len = 1024
                body = json.dumps(response.json())
                self.assertGreater(len(body), prefix_len)
                if debug == 0:
                    expected_log = "… with response body of type (<class 'dict'>)"
                elif debug == 1:
                    expected_log = f'… with a response body starting in {body[:prefix_len]!r}'
                elif debug > 1:
                    expected_log = f'… with the 9118 byte long response body {body!r}'
                else:
                    assert False
                self.assertEqual(expected_log, body_log_message)
                self.assertEqual(INFO, body_log_level)
                self.assertEqual(200, response.status_code)
