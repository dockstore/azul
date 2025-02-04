"""
Copy a file in an HCA catalog from TDR to the current deployment's storage
bucket and print a signed URL to the file's destination. Authentication is not
supported, so the file must be publicly accessible.
"""
import argparse
import logging
import math
import sys

from azul import (
    CatalogName,
    config,
)
from azul.args import (
    AzulArgumentHelpFormatter,
)
from azul.azulclient import (
    AzulClient,
)
from azul.drs import (
    AccessMethod,
)
from azul.http import (
    http_client,
)
from azul.logging import (
    configure_script_logging,
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
from azul.service.storage_service import (
    StorageService,
)
from azul.types import (
    JSON,
    MutableJSON,
)

log = logging.getLogger(__name__)

http = http_client(log)


def get_file(catalog: CatalogName, file_uuid: str) -> MutableJSON:
    source_ids = SourceService().list_source_ids(catalog, authentication=None)
    filters = Filters(explicit={},
                      source_ids=source_ids)
    file = RepositoryService().get_data_file(catalog=catalog,
                                             file_uuid=file_uuid,
                                             file_version=None,
                                             filters=filters)
    if file is None:
        raise RuntimeError(f'File {file_uuid!r} not found in catalog {catalog!r}')
    return file


def get_download_url(catalog: CatalogName, file: JSON) -> str:
    drs_uri = file['drs_uri']
    drs = AzulClient().repository_plugin(catalog).drs_client()
    access = drs.get_object(drs_uri, AccessMethod.gs)
    assert access.method is AccessMethod.https, access
    return access.url


def object_key(file: JSON) -> str:
    # For non-HCA catalogs, a different hash may be more appropriate
    return f'file/{file["sha256"]}.sha256'


def mirror_file(catalog: CatalogName, file_uuid: str, part_size: int) -> str:
    assert config.is_tdr_enabled(catalog), 'Only TDR catalogs are supported'
    assert config.is_hca_enabled(catalog), 'Only HCA catalogs are supported'
    file = get_file(catalog, file_uuid)
    download_url = get_download_url(catalog, file)
    key = object_key(file)
    storage = StorageService()
    upload = storage.create_multipart_upload(key, content_type=file['content-type'])

    total_size = file['size']
    part_count = math.ceil(total_size / part_size)
    assert part_count <= 10000, (total_size, part_size, part_count)

    def file_part(part_number: int) -> str:
        start = part_number * part_size
        end = min((part_number + 1) * part_size - 1, total_size)
        response = http.request('GET',
                                download_url,
                                headers={'Range': f'bytes={start}-{end}'})
        if response.status == 206:
            return storage.upload_multipart_part(response.data,
                                                 part_number + 1,
                                                 upload)
        else:
            raise RuntimeError('Unexpected response from repository', response.status)

    etags = list(map(file_part, range(part_count)))
    storage.complete_multipart_upload(upload, etags)
    return storage.get_presigned_url(key, file['name'])


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=AzulArgumentHelpFormatter)
    parser.add_argument('-c', '--catalog')
    parser.add_argument('-f', '--file-uuid')
    parser.add_argument('-p', '--part-size', type=int, default=50 * 2 ** 20)
    args = parser.parse_args(argv)
    signed_url = mirror_file(args.catalog, args.file_uuid, args.part_size)
    print(signed_url)


if __name__ == '__main__':
    configure_script_logging(log)
    main(sys.argv[1:])
