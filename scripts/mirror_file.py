"""
Copy a file in an HCA catalog from TDR to the current deployment's mirroring
bucket and print a signed URL to the file's destination. Authentication is not
supported, so the file must be publicly accessible.
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
from azul.http import (
    http_client,
)
from azul.indexer.mirror_service import (
    FilePart,
    MirrorService,
)
from azul.logging import (
    configure_script_logging,
)
from azul.plugins.metadata.hca import (
    HCAFile,
)
from azul.service import (
    Filters,
)
from azul.service.repository_service import (
    RepositoryService,
)
from azul.service.source_service import (
    SourceService,
)

log = logging.getLogger(__name__)

http = http_client(log)


def get_file(catalog: CatalogName, file_uuid: str) -> HCAFile:
    source_ids = SourceService().list_source_ids(catalog, authentication=None)
    filters = Filters(explicit={},
                      source_ids=source_ids)
    file = RepositoryService().get_data_file(catalog=catalog,
                                             file_uuid=file_uuid,
                                             file_version=None,
                                             filters=filters)
    if file is None:
        raise RuntimeError(f'File {file_uuid!r} not found in catalog {catalog!r}')
    assert isinstance(file, HCAFile)
    return file


def mirror_file(catalog: CatalogName, file_uuid: str, part_size: int) -> str:
    assert config.enable_mirroring, R('Mirroring must be enabled')
    assert config.is_tdr_enabled(catalog), R('Only TDR catalogs are supported')
    file = get_file(catalog, file_uuid)
    service = MirrorService()
    upload_id = service.begin_mirroring_file(file)

    def mirror_parts():
        part = FilePart.first(file, part_size)
        while part is not None:
            yield service.mirror_file_part(catalog, file, part, upload_id)
            part = part.next(file)

    etags = list(mirror_parts())
    service.finish_mirroring_file(file, upload_id, etags=etags)
    return service.get_mirror_url(file)


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=AzulArgumentHelpFormatter)
    parser.add_argument('-c', '--catalog', default=config.default_catalog)
    parser.add_argument('-f', '--file-uuid')
    parser.add_argument('-p', '--part-size', type=int, default=FilePart.default_size)
    args = parser.parse_args(argv)
    signed_url = mirror_file(args.catalog, args.file_uuid, args.part_size)
    print(signed_url)


if __name__ == '__main__':
    configure_script_logging(log)
    main(sys.argv[1:])
