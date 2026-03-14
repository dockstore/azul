import doctest
import unittest

import azul
import azul.auth
import azul.csp
import azul.docker
import azul.doctests
import azul.drs
import azul.dss
import azul.files
import azul.http
import azul.indexer
import azul.indexer.aggregate
import azul.indexer.document
import azul.indexer.repository_service
import azul.json
import azul.json_freeze
import azul.lib.attrs
import azul.lib.bigquery
import azul.lib.bytes
import azul.lib.caching
import azul.lib.collections
import azul.lib.exceptions
import azul.lib.functions
import azul.lib.iterators
import azul.lib.objects
import azul.lib.strings
import azul.lib.threads
import azul.lib.time
from azul.logging import (
    configure_test_logging,
)
from azul.modules import (
    load_module,
    load_script,
)
import azul.openapi
import azul.openapi.params
import azul.openapi.responses
import azul.openapi.schema
import azul.plugins
import azul.plugins.metadata.hca.indexer.transform
import azul.plugins.metadata.hca.service.contributor_matrices
import azul.plugins.repository.canned
import azul.plugins.repository.tdr_hca
import azul.service.controller
import azul.service.drs_controller
import azul.service.manifest_service
import azul.service.repository_controller
import azul.terra
import azul.terraform
import azul.types
import azul.uuids
import azul.vendored.frozendict
import service


# noinspection PyPep8Naming
def setUpModule():
    configure_test_logging()


def load_tests(_loader,
               tests: unittest.TestSuite,
               _ignore
               ) -> unittest.TestSuite:
    root = azul.config.project_root
    for module in [
        azul,
        azul.auth,
        azul.csp,
        azul.doctests,
        azul.docker,
        azul.drs,
        azul.dss,
        azul.files,
        azul.http,
        azul.indexer,
        azul.indexer.aggregate,
        azul.indexer.document,
        azul.indexer.repository_service,
        azul.json,
        azul.json_freeze,
        azul.lib.attrs,
        azul.lib.bigquery,
        azul.lib.bytes,
        azul.lib.caching,
        azul.lib.collections,
        azul.lib.exceptions,
        azul.lib.functions,
        azul.lib.iterators,
        azul.lib.objects,
        azul.lib.strings,
        azul.lib.threads,
        azul.lib.time,
        azul.openapi,
        azul.openapi.params,
        azul.openapi.responses,
        azul.openapi.schema,
        azul.plugins,
        azul.plugins.metadata.hca.service.contributor_matrices,
        azul.plugins.repository.canned,
        azul.plugins.repository.tdr_hca,
        azul.plugins.metadata.hca.indexer.transform,
        azul.service.controller,
        azul.service.drs_controller,
        azul.service.manifest_service,
        azul.service.repository_controller,
        azul.terra,
        azul.terraform,
        azul.types,
        azul.uuids,
        azul.vendored.frozendict,
        load_script('can_bundle'),
        load_script('envhook'),
        load_script('export_environment'),
        load_module(root + '/.flake8/azul_flake8.py', 'azul_flake8'),
        load_module(root + '/.github/workflows/schedule.py', 'schedule'),
        service
    ]:
        suite = doctest.DocTestSuite(module)
        assert suite.countTestCases() > 0, module
        tests.addTests(suite)
    return tests


if __name__ == '__main__':
    setUpModule()
    runner = unittest.TextTestRunner(verbosity=2)
    runner.run(load_tests(None, unittest.TestSuite(), None))
