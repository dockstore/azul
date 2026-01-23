"""
Downloads the license files for each of the python packages listed in
`requirements.all.txt`.

Recommended usage when updating the current set of license files:

1) Move the existing license files out of the destination path and into a
   temporary location.
2) Run this script to download fresh copies of the license files.
3) Using the old license files for reference, manually download the licenses
   for the python packages that this script failed to locate.
4) Delete the old license files.
"""
import json
import logging
from typing import (
    Sequence,
)

from furl import (
    furl,
)
from urllib3 import (
    HTTPResponse,
)

from azul import (
    cached_property,
    config,
)
from azul.http import (
    http_client,
)
from azul.logging import (
    configure_script_logging,
)

log = logging.getLogger(__name__)


class Main:
    destination_path = f'{config.project_root}/docs/licenses/python/'

    file_names = [
        'LICENSE',
        'LICENSE.txt',
        'LICENSE.rst',
        'LICENSE.md',
        'LICENSE.mit',
        'COPYING',
        'COPYING.BSD',
        'LICENCE',
        'LICENCE.md'
    ]

    @cached_property
    def http(self):
        return http_client()

    def main(self):
        failures = []

        with open(f'{config.project_root}/requirements.all.txt', 'r') as f:
            lines = f.readlines()

        for line in lines:
            if line:
                found = False
                package, version = line.split('==')
                pypi_url = f'https://pypi.org/pypi/{package}/json'
                response = self.http.request('GET', pypi_url)
                assert isinstance(response, HTTPResponse)
                # Not all requirements are found on pypi (e.g. resumablehash)
                if response.status == 200:
                    urls = json.loads(response.data)['info']['project_urls']
                    urls = [] if urls is None else github_urls(urls.values())
                    for url in urls:
                        for filename in self.file_names:
                            response = self.http.request('GET', f'{url}/raw/HEAD/{filename}')
                            assert isinstance(response, HTTPResponse)
                            if response.status == 200:
                                file_path = f'{self.destination_path}{package}.txt'
                                with open(file_path, 'wb') as f:
                                    f.write(f'{url}/{filename}\n\n'.encode('ascii'))
                                    f.write(response.data)
                                log.info('%s... SUCCESS', package)
                                found = True
                                break
                        if found:
                            break
                if not found:
                    failures.append(package)
                    log.info('%s... FAIL (%s)', package, pypi_url)

        if failures:
            log.error('Failed to fetch licenses for packages: %s', failures)

    def github_urls(self, urls: Sequence[str]) -> set[str]:
        """
        Return URLs to GitHub project home directories found in the URLs given.
        """
        urls_ = set()
        for url in urls:
            url_ = furl(url.rstrip('/'))
            if url_.netloc == 'github.com':
                last_segment = url_.path.segments[-1] if url_.path.segments else ''
                if last_segment == 'issues':
                    # https://github.com/USER/PACKAGE/issues
                    url_.path.segments.pop()
                elif last_segment.endswith('.git'):
                    # https://github.com/googleapis/proto-plus-python.git
                    url_.path.segments[-1] = last_segment[:-4]
                urls_.add(str(url_))
        return urls_


if __name__ == '__main__':
    configure_script_logging(log)
    Main().main()
