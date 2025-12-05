import argparse
import logging
import shutil

from azul import (
    CatalogName,
    config,
)

log = logging.getLogger(__name__)


class AzulArgumentHelpFormatter(argparse.ArgumentDefaultsHelpFormatter):

    def __init__(self, prog: str):
        super().__init__(prog,
                         max_help_position=50,
                         width=min(shutil.get_terminal_size((80, 25)).columns, 120))


def get_catalogs(catalogs_arg: list[str] | None) -> list[CatalogName]:
    if catalogs_arg is None:
        current_catalog = config.current_catalog
        if current_catalog is None:
            catalog_origin = 'deployment configuration (no specific catalogs provided)'
            catalogs = [
                catalog.name
                for catalog in config.catalogs.values()
                if not catalog.is_integration_test_catalog
            ]
        else:
            catalog_origin = 'environment variable'
            catalogs = [config.catalogs[current_catalog].name]
    else:
        catalog_origin = 'command line argument'
        catalogs = catalogs_arg
    log.info('Using catalog(s) specified via %s: %r', catalog_origin, catalogs)
    return catalogs


def get_sources(sources_arg: list[str] | None) -> set[str]:
    if sources_arg is None:
        current_sources = config.current_sources
        if current_sources is None:
            source_origin = 'deployment configuration (no specific sources provided)'
            source_globs = {'*'}
        else:
            source_origin = 'environment variable'
            source_globs = set(current_sources)
    else:
        source_origin = 'command line argument'
        source_globs = set(sources_arg)
    log.info('Using source glob(s) specified via %s: %r', source_origin, source_globs)
    return source_globs
