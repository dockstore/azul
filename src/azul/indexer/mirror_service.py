import json
import logging
import math
import string
import time
from typing import (
    ClassVar,
    Self,
    Sequence,
    TYPE_CHECKING,
)

import attr
import attrs

from azul import (
    CatalogName,
    JSON,
    R,
    cache,
    cached_property,
    config,
)
from azul.attrs import (
    SerializableAttrs,
)
from azul.collections import (
    OrderedSet,
)
from azul.deployment import (
    aws,
)
from azul.digests import (
    Hasher,
    get_resumable_hasher,
)
from azul.drs import (
    AccessMethod,
)
from azul.http import (
    HasCachedHttpClient,
)
from azul.plugins import (
    File,
    RepositoryPlugin,
)
from azul.service.storage_service import (
    StorageObjectNotFound,
    StorageService,
)

if TYPE_CHECKING:
    from mypy_boto3_s3.service_resource import (
        MultipartUpload,
    )

log = logging.getLogger(__name__)


@attrs.frozen(auto_attribs=True, kw_only=True)
class FilePart(SerializableAttrs):
    """
    A part of a mirrored file
    """
    #: The part number, starting at 0 for the first part, unlike S3 API part
    #: numbers, which start at 1.
    #:
    index: int

    #: Offset of the first byte of this part, relative to the start of the file
    offset: int

    #: The size of this part
    #:
    size: int

    #: Various S3 quotas related to parts and part sizes
    #: https://docs.aws.amazon.com/AmazonS3/latest/userguide/qfacts.html
    #:
    min_size: ClassVar[int] = 5 * 1024 ** 2
    max_size: ClassVar[int] = 5 * 1024 ** 3
    max_num_parts: ClassVar[int] = 10000

    #: We observe a download rate of ~14 MB/s. Download time should ideally be
    #: 1/4 of the Lambda timeout. Since we track the ETag of each part in SQS
    #: messages, message size becomes another constraint: we observe ETags to be
    #: 32 byte hexadecimal strings which, if represented in a JSON array, take
    #: up 35 bytes per item, 36 if the comma is followed by a space. With a
    #: maximum SQS message size of 256 KiB, we can store approximately 7280
    #: ETags in an SQS messages, so the largest file we can mirror using a part
    #: size of 256 MiB is 1.5 TiB.
    #:
    default_size: ClassVar[int] = 256 * 1024 ** 2

    @classmethod
    def first(cls, file: File, part_size: int) -> Self:
        """
        The first part of the given file, using the given part size.
        """
        assert file.size is not None, R(
            'File size unknown', file)
        assert cls.min_size <= part_size <= cls.max_size, R(
            'Invalid part size', part_size)
        part_count = math.ceil(file.size / part_size)
        assert part_count <= cls.max_num_parts, R(
            'Part size is too small for this file', part_size, file)
        return cls(index=0, offset=0, size=min(part_size, file.size))

    def next(self, file: File) -> Self | None:
        """
        The part following this part in the given file, or None if this is the
        last part.
        """
        assert file.size is not None, R('File size unknown', file)
        next_offset = self.offset + self.size
        if next_offset == file.size:
            return None
        elif 0 < next_offset < file.size:
            next_index = self.index + 1
            next_size = min(self.size, file.size - next_offset)
            return attr.evolve(self, index=next_index, offset=next_offset, size=next_size)
        else:
            assert False, R('Part range exceeds file size', self, file)


class MirrorService(HasCachedHttpClient):

    @cached_property
    def _storage(self) -> StorageService:
        return StorageService(bucket_name=aws.mirror_bucket)

    @cache
    def repository_plugin(self, catalog: CatalogName) -> RepositoryPlugin:
        return RepositoryPlugin.load(catalog).create(catalog)

    def mirror_file(self, catalog: CatalogName, file: File):
        """
        Upload the file in a single request. For larger files, use
        :meth:`begin_mirroring_file` instead.
        """
        if self._check_info(file):
            log.info('File %r is already mirrored, skipping upload', file.uuid)
        else:
            file_content = self._download(catalog, file)
            self._storage.put(object_key=self.mirror_object_key(file),
                              data=file_content,
                              content_type=file.content_type)
            _, digest_type = file.digest()
            hasher = get_resumable_hasher(digest_type)
            hasher.update(file_content)
            self._verify_digest(file, hasher)
            self._put_info(file)

    def begin_mirroring_file(self, file: File) -> str:
        """
        Initiate a multipart upload of the file's content and return the upload
        ID.
        """
        upload = self._storage.create_multipart_upload(object_key=self.mirror_object_key(file),
                                                       content_type=file.content_type)
        return upload.id

    def mirror_file_part(self,
                         catalog: CatalogName,
                         file: File,
                         part: FilePart,
                         upload_id: str,
                         hasher: Hasher
                         ) -> str:
        """
        Upload a part of a file to a multipart upload begun with
        :meth:`begin_mirroring_file` and return the uploaded part's ETag.
        The provided hasher is mutated to incorporated the part's content.
        """
        upload = self._get_upload(file, upload_id)
        file_content = self._download(catalog, file, part)
        hasher.update(file_content)
        return self._storage.upload_multipart_part(file_content,
                                                   part.index + 1,
                                                   upload)

    def finish_mirroring_file(self,
                              file: File,
                              upload_id: str,
                              etags: Sequence[str],
                              hasher: Hasher
                              ):
        """
        Complete a multipart upload begun with :meth:`begin_mirroring_file`.
        """
        upload = self._get_upload(file, upload_id)
        self._storage.complete_multipart_upload(upload, etags)
        self._verify_digest(file, hasher)
        self._check_info(file)
        self._put_info(file)

    def list_info_objects(self, prefix: str) -> OrderedSet[str]:
        return self._storage.list('info/' + prefix)

    def get_mirror_url(self, file: File) -> str:
        return self._storage.get_presigned_url(key=self.mirror_object_key(file),
                                               file_name=file.name)

    def _check_info(self, file: File) -> bool:
        key = self.info_object_key(file)
        try:
            content = self._storage.get(key)
        except StorageObjectNotFound:
            return False
        else:
            content_type = json.loads(content)['content_type']
            assert content_type == file.content_type, R(
                'Conflicting content type', file.uuid, key, content_type, file.content_type)
            return True

    def info_object(self, file: File) -> JSON:
        return {
            'content_type': file.content_type,
        }

    def _put_info(self, file: File):
        key = self.info_object_key(file)
        content = self.info_object(file)
        self._storage.put(object_key=key,
                          data=json.dumps(content).encode(),
                          content_type='application/json')

    def mirror_object_key(self, file: File) -> str:
        return self._file_key('file', file)

    def info_object_key(self, file: File) -> str:
        return self._file_key('info', file, extension='.json')

    def _file_key(self, prefix: str, file: File, *, extension: str = '') -> str:
        digest, digest_type = file.digest()
        assert all(c in string.hexdigits for c in digest), R(
            'Expected a hexadecimal digest', digest)
        return f'{prefix}/{digest.lower()}.{digest_type}{extension}'

    @cache
    def _get_repository_url(self, catalog: CatalogName, file: File):
        assert config.is_tdr_enabled(catalog), R('Only TDR catalogs are supported', catalog)
        assert file.drs_uri is not None, R('File cannot be downloaded', file.uuid)
        drs = self.repository_plugin(catalog).drs_client(authentication=None)
        access = drs.get_object(file.drs_uri, AccessMethod.gs)
        assert access.method is AccessMethod.https, access
        return access.url

    def _download(self,
                  catalog: CatalogName,
                  file: File,
                  part: FilePart | None = None
                  ) -> bytes:
        download_url = self._get_repository_url(catalog, file)
        start = time.time()
        if part is None:
            headers = {}
            size = file.size
            expected_status = 200
        else:
            headers = {'Range': f'bytes={part.offset}-{part.offset + part.size - 1}'}
            size = part.size
            expected_status = 206
        # Ideally we would stream the response, but boto only supports uploading
        # from streams that are seekable.
        response = self._http_client.request('GET', download_url, headers=headers)
        if response.status == expected_status:
            log.info('Downloaded %d bytes of file %r in %.3fs',
                     size, file.uuid, time.time() - start)
            return response.data
        else:
            raise RuntimeError('Unexpected response from repository', response.status)

    def _get_upload(self, file: File, upload_id: str) -> 'MultipartUpload':
        return self._storage.load_multipart_upload(object_key=self.mirror_object_key(file),
                                                   upload_id=upload_id)

    def _verify_digest(self, file: File, hasher: Hasher):
        expected_digest_value, digest_type = file.digest()
        actual_digest_value = hasher.hexdigest()
        assert expected_digest_value == actual_digest_value, R(
            'File digest value does not match its contents',
            file.uuid, digest_type, expected_digest_value, actual_digest_value)
