"""
Command line utility to validate snapshots prior to indexing by Azul
"""

import argparse
import json
import logging
import sys

from azul import (
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
from azul.plugins.repository.tdr import (
    TDRPlugin,
)
from azul.terra import (
    TDRSourceSpec,
)
from azul.types import (
    JSON,
)

log = logging.getLogger(__name__)
configure_script_logging(log)


def main(args):
    invalid_sources: list[JSON] = list()

    azul = AzulClient(num_workers=1)
    sources_by_catalog = azul.matching_sources(args.catalogs, set(args.sources))
    previous_sources: set[str] = set()
    for catalog, sources in sources_by_catalog.items():
        plugin = azul.repository_plugin(catalog)
        assert isinstance(plugin, TDRPlugin)
        log.info('Checking for %r in catalog %s', args.match, catalog)
        for source_str in sources:
            if source_str not in previous_sources:
                source = TDRSourceSpec.parse(source_str)
                invalid_sources.extend(plugin.find_in_source(source, args.match))
                previous_sources.add(source_str)
    print()
    if invalid_sources:
        print(json.dumps(invalid_sources, indent=4))
    else:
        print('Checked snapshots OK')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=AzulArgumentHelpFormatter)
    parser.add_argument('--catalogs',
                        nargs='+',
                        metavar='CATALOG',
                        default=[
                            catalog.name
                            for catalog in config.catalogs.values()
                            if not catalog.is_integration_test_catalog
                        ]
                        if config.current_catalog is None else
                        [
                            config.catalogs[config.current_catalog].name
                        ],
                        choices=config.catalogs,
                        help='The names of the catalogs to validate.')
    parser.add_argument('--match',
                        metavar='STR_MATCH',
                        default='||',
                        help='The string pattern to match.')
    parser.add_argument('--sources',
                        default=config.current_sources,
                        nargs='+',
                        metavar='SNAPSHOT_SEQ',
                        help='Limit scan to selected catalog(s). '
                             'Supports shell-style wildcards to match multiple sources per argument.')

    args = parser.parse_args(sys.argv[1:])
    main(args)
