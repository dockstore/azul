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

from app_test_case import (
    LocalAppTestCase,
)
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


# noinspection PyPep8Naming
def setUpModule():
    configure_test_logging()


class TestServiceAppLogging(DCP1CannedBundleTestCase, LocalAppTestCase):

    @classmethod
    def lambda_name(cls) -> str:
        return 'service'

    def test_request_logs(self):
        for azul_debug in (0, 1, 2):
            level = [INFO, DEBUG, DEBUG][azul_debug]
            for authenticated in False, True:
                with self.subTest(azul_debug=azul_debug, authenticated=authenticated):
                    url = self.base_url.set(path='/health/basic')
                    request_headers = {'authorization': 'Bearer foo_token'} if authenticated else {}
                    with self.assertLogs(logger=log, level=level) as logs:
                        debug = PropertyMock(return_value=azul_debug)
                        with patch.object(Config, 'debug', new=debug):
                            requests.get(str(url), headers=request_headers)
                    logs = [(r.levelno, r.getMessage()) for r in logs.records]
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
                                "Received GET request for '/health/basic', "
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
                            ),
                            (
                                INFO,
                                '… with response body not empty' if azul_debug == 0 else
                                '… with response body \'{"up": true}\''
                            )
                        ],
                        logs
                    )
