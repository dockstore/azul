"""
Copy all files from the public sources in a catalog to the current deployment's
mirroring bucket. The actual file-copying is not yet implemented, so all this
currently does is send messages to the indexer app that don't do anything.
"""
import argparse
import logging
import sys

from azul import (
    CatalogName,
    R,
    config,
)
from azul.args import (
    AzulArgumentHelpFormatter,
)
from azul.azulclient import (
    AzulClient,
)
from azul.logging import (
    configure_script_logging,
)

log = logging.getLogger(__name__)


def mirror_catalog(catalog: CatalogName, wait: bool):
    azul = AzulClient()
    plugin = azul.repository_plugin(catalog)
    assert azul.is_queue_empty(config.mirror_queue.name), R(
        'A mirroring operation is already in progress. The current operation '
        'must finish before another can begin.')
    fail_queue = config.mirror_queue.fail.name
    if not azul.is_queue_empty(fail_queue):
        log.warning('Failed messages from a previous operation are still '
                    'present in %r. If they are not purged, this operation'
                    'may exit with an error status, even if no new errors '
                    'occur.', fail_queue)
    public_sources = plugin.list_sources(authentication=None)
    azul.remote_mirror(catalog, public_sources)
    if wait:
        azul.wait_for_mirroring()
        assert azul.is_queue_empty(fail_queue), R(
            'Failures occurred: there are messages in %r', fail_queue)


def main(args):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=AzulArgumentHelpFormatter)
    parser.add_argument('-c', '--catalog', default=config.default_catalog)
    parser.add_argument('--no-wait', action='store_false', dest='wait')
    args = parser.parse_args(args)
    assert config.enable_mirroring, R('Mirroring is not enabled')
    mirror_catalog(args.catalog, args.wait)


if __name__ == '__main__':
    configure_script_logging(log)
    main(sys.argv[1:])
