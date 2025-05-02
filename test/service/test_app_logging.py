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
import azul.logging

from indexer import (
    DCP1CannedBundleTestCase,
)
from service import (
    WebServiceTestCase,
)


# noinspection PyPep8Naming
def setUpModule():
    azul.logging.configure_test_logging()


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
        for azul_debug in (0, 1, 2):
            level = [INFO, DEBUG, DEBUG][azul_debug]
            for authenticated in False, True:
                with self.subTest(azul_debug=azul_debug, authenticated=authenticated):
                    url = self.base_url.set(path='/index/projects')
                    request_headers = {'authorization': 'Bearer foo_token'} if authenticated else {}
                    with self.assertLogs(logger=log, level=level) as logs:
                        debug = PropertyMock(return_value=azul_debug)
                        with patch.object(Config, 'debug', new=debug):
                            with patch.object(azul.logging, 'http_body_log_prefix_len', 128):
                                requests.get(str(url), headers=request_headers)
                    logs = [(r.levelno, r.getMessage()) for r in logs.records]
                    last_log_level, last_log = logs.pop()  # … to validate separately
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
                    expected_body = json.dumps(
                        {
                            'pagination': {
                                'count': 1,
                                'total': 1,
                                'size': 10,
                                'next': None,
                                'previous': None,
                                'pages': 1,
                                'sort': 'projectTitle',
                                'order': 'asc'
                            }
                        }
                    )
                    self.assertEqual(
                        [
                            (
                                INFO,
                                "Received GET request for '/index/projects', "
                                f'with {json.dumps(dict(query=None, headers=request_headers))}.'),
                            (
                                INFO,
                                "Authenticated request as OAuth2(access_token='foo_token')"
                                if authenticated else
                                'Did not authenticate request.'
                            ),
                            (
                                INFO,
                                'Returning 200 response with headers ' +
                                json.dumps(response_headers) + '.'
                            )
                        ],
                        logs
                    )
                    if azul_debug == 0:
                        self.assertEqual('… with response body not empty', last_log)
                    elif azul_debug == 1:
                        self.assertEqual(f'… with response body {expected_body[:128]!r}', last_log)
                    elif azul_debug > 1:
                        self.assertTrue(last_log.startswith(f'… with response body \'{expected_body[:135]}'))
                    self.assertEqual(INFO, last_log_level)
