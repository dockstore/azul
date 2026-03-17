import os
import time
from unittest import (
    mock,
)

from azul import (
    config,
)
from azul.docker import (
    resolve_docker_image_for_launch,
)
from azul.lib.json_freeze import (
    freeze,
    sort_frozen,
)
from azul.logging import (
    get_test_logger,
    silenced_open_search_logger,
)
from azul.opensearch import (
    OpenSearchClientFactory,
)
from docker_container_test_case import (
    DockerContainerTestCase,
)

log = get_test_logger(__name__)


class OpenSearchTestCase(DockerContainerTestCase):
    """
    A test case that uses an OpenSearch instance running in a container.
    The same OpenSearch instance will be shared by all tests in the class.
    """
    open_search = None
    _env_patch = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        image = resolve_docker_image_for_launch('opensearch')
        endpoint = cls._create_container(image=image,
                                         container_port=9200,
                                         cached=True,
                                         environment=['discovery.type=single-node',
                                                      'DISABLE_INSTALL_DEMO_CONFIG=true',
                                                      'DISABLE_SECURITY_PLUGIN=true',
                                                      'DISABLE_SECURITY_DASHBOARDS_PLUGIN=true',
                                                      'ES_JAVA_OPTS=-Xms512m -Xmx512m',
                                                      'indices.breaker.total.use_real_memory=false'])
        try:
            new_env = config.open_search_endpoint_env(endpoint=endpoint,
                                                      instance_count=2)
            cls._env_patch = mock.patch.dict(os.environ, **new_env)
            cls._env_patch.start()
            cls.open_search = OpenSearchClientFactory.get()
            cls._wait_for_es()

            # Disable the automatic creation of indexes when documents are
            # indexed. We create indexes explicitly before any documents are
            # indexed so a missing index would be indicative of some sort of
            # bug. We want to fail early in that situation. Automatically
            # created indices have a only a default mapping, resulting in
            # failure modes that are harder to diagnose.
            #
            cls.open_search.cluster.put_settings(body={
                'persistent': {
                    'action.auto_create_index': False,
                    'action.destructive_requires_name': False
                }
            })
        except BaseException:  # no coverage
            cls._kill_containers(keep_cached=False)
            raise

    @classmethod
    def _wait_for_es(cls):
        start_time = time.time()
        with silenced_open_search_logger():
            while not cls.open_search.ping():
                assert time.time() - start_time < 60, 'Docker container timed out'
                log.debug('Could not ping OpenSearch. Retrying...')
                time.sleep(1)
        log.info(f'It took {time.time() - start_time:.3f}s for ES container to boot up')

    def assertElasticEqual(self, first, second):
        """
        The ordering of list items in our OpenSearch responses typically
        doesn't matter. The comparison done by this method is insensitive to
        ordering differences in lists.

        For details see the doc string for sort_frozen() and freeze()
        """
        self.assertEqual(sort_frozen(freeze(first)), sort_frozen(freeze(second)))

    @classmethod
    def tearDownClass(cls):
        cls._env_patch.stop()
        super().tearDownClass()
