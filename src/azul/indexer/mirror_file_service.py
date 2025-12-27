import json
import logging
import math
import string
import time
from typing import (
    ClassVar,
    Protocol,
    Self,
    Sequence,
    TYPE_CHECKING,
)

import attr
import attrs
from furl import (
    furl,
)

from azul import (
    CatalogName,
    R,
    cached_property,
    config,
    mutable_furl,
)
from azul.attrs import (
    SerializableAttrs,
)
from azul.auth import (
    Authentication,
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
    RepositoryFileDownload,
    RepositoryPlugin,
)
from azul.service.storage_service import (
    StorageObjectNotFound,
    StorageService,
)
from azul.types import (
    JSON,
)

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


@attrs.frozen(kw_only=True)
class FilePart(SerializableAttrs):
    """
    A part of a mirrored file
    """

    #: The part number, starting at 0 for the first part, unlike S3 API part
    #: numbers, which start at 1.
    #:
    index: int

    #: Offset of the first byte of this part, relative to the start of the file
    #:
    offset: int

    #: The size of this part
    #:
    size: int

    #: Various S3 quotas related to parts and part sizes
    #: https://docs.aws.amazon.com/AmazonS3/latest/userguide/qfacts.html
    #:
    min_size: ClassVar[int] = aws.s3_min_part_size
    max_size: ClassVar[int] = aws.s3_max_part_size
    max_num_parts: ClassVar[int] = aws.s3_max_num_parts

    #: In experiments, we observed a download rate of ~25 MB/s for an AWS Lambda
    #: function downloading from GCS. To leave room for network impairments or
    #: other partial outages, the normal download time for a single part should
    #: be one third of the Lambda function timeout. Also, we heuristically
    #: decided for the part size to not exceed 1 GiB in size in stable
    # deployments, or 256 MiB in elsewhere.
    #:
    default_size: ClassVar[int] = min(
        1024 ** 3 if config.deployment.is_stable else 256 * 1024 ** 2,
        int(config.mirror_lambda_timeout * 25 * 1024 ** 2 / 3)
    )

    assert min_size <= default_size <= max_size

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


@attrs.frozen(kw_only=True)
class MirrorFileDownload(RepositoryFileDownload):
    _location: str

    @property
    def retry_after(self) -> int | None:
        return None

    @property
    def location(self) -> str | None:
        return self._location

    def update(self,
               plugin: RepositoryPlugin,
               authentication: Authentication | None
               ) -> None:
        pass


@attrs.frozen(kw_only=True, slots=False)
class BaseMirrorFileService:
    """
    Service for reading mirrored files, plus some test support. The most
    prominent reader of mirrored files is the service app.
    """

    catalog: CatalogName

    @cached_property
    def _storage(self) -> StorageService:
        bucket = config.mirror_bucket
        if bucket is None or self.catalog in config.integration_test_catalogs:
            bucket = aws.mirror_bucket
        return StorageService(bucket)

    #: Since we track the ETags of all parts of a multipart file in SQS
    #: messages, the maximum file size is primarily constrained by the SQS
    #: message size. We observe ETags to be 32-byte hexadecimal strings which,
    #: if represented in a JSON array, take up 35 bytes per item, 36 if the
    #: comma is followed by a space. This limits the number of ETags we can
    #: store in a message, while leaving room for 64 KiB of other information,
    #: and thereby also limits the maximum size of files we can mirror.
    #:
    max_file_size = (
        FilePart.default_size
        * (aws.sqs_max_message_size - 64 * 1024) / 36
    )

    # We should be able to copy files 1.5 TiB in size or larger. Currently, the
    # largest file in the open-access datasets for AnVIL is 1.3 TiB.
    #
    assert 1.5 * 1024 ** 4 <= max_file_size

    def mirror_uri(self, file: File) -> str:
        """
        Speculative S3 URI of the given file. No check is performed to see if
        the file is currently mirrored, so there is no guarantee that requests
        to the URI will succeed.
        """
        return str(furl(scheme='s3',
                        netloc=self._storage.bucket_name,
                        path=self._file_object_key(file)))

    def mirror_url(self, file: File) -> str:
        return self._storage.get_presigned_url(key=self._file_object_key(file),
                                               file_name=file.name,
                                               content_type=file.content_type)

    def info_exists(self, file: File) -> bool:
        return self._get_info(file) is not None

    def file_exists(self, file: File) -> bool:
        try:
            self._storage.head_object(self._file_object_key(file))
        except StorageObjectNotFound:
            return False
        else:
            return True

    def _get_info(self, file: File) -> JSON | None:
        key = self._info_object_key(file)
        try:
            content = self._storage.get_object(key)
        except StorageObjectNotFound:
            return None
        else:
            json_content = json.loads(content)
            content_type = json_content['content-type']
            if content_type != file.content_type:
                # FIXME: Content type in mirror info objects inconsistent with index
                #        https://github.com/DataBiosphere/azul/issues/7193
                log.warning('Conflicting content type %r for file %r', content_type, file)
            return json_content

    info_prefix, file_prefix = 'info', 'file'

    def _info_object_key(self, file: File) -> str:
        return self._object_key(self.info_prefix, file, extension='.json')

    def _file_object_key(self, file: File) -> str:
        return self._object_key(self.file_prefix, file)

    def _object_key(self, prefix: str, file: File, *, extension: str = '') -> str:
        digest = file.digest
        digest_value = digest.value.lower()
        assert all(c in string.hexdigits for c in digest_value), R(
            'Expected a hexadecimal digest', digest)
        mirror_prefix = self._mirror_prefix
        return f'{mirror_prefix}{prefix}/{digest_value}.{digest.type}{extension}'

    @cached_property
    def _mirror_prefix(self) -> str:
        return '_it/' if self.catalog in config.integration_test_catalogs else ''

    def delete_it_files(self):
        """
        Delete all objects (both file/ and info/) with the given catalog's
        mirror prefix. Currently, the mirror prefix is only used to distinguish
        IT catalogs from non-IT catalogs, so if an IT catalog is specified,
        objects from *all* IT catalogs will be deleted, not just the specified
        catalog.
        """
        assert self.catalog in config.integration_test_catalogs, R(
            'Not an IT catalog', self.catalog)
        prefix = self._mirror_prefix
        assert len(prefix) > 1 and prefix.endswith('/'), prefix
        keys = self._storage.list(prefix)
        assert len(keys) <= 300, R('Too many objects', len(keys))
        self._storage.delete_objects(keys, batch_size=100)


class SchemaUrlFunc(Protocol):

    def __call__(self, *, schema_name: str, version: int) -> mutable_furl: ...


@attrs.frozen(kw_only=True, slots=False)
class MirrorFileService(BaseMirrorFileService, HasCachedHttpClient):
    """
    Service for writing mirrored files. Requires a mechanism to compose schema
    URLs. This function is currently offered by the indexer app, so another way
    to view this service class is as an encapsulation of the mirroring work done
    by the indexer app.
    """

    _schema_url_func: SchemaUrlFunc

    # We don't store the mirrored files' actual content type(s) in S3's
    # `Content-Type` metadata because a single file object may store the
    # contents of multiple file metadata entities, which may declare different
    # content types for the same data. When file objects are downloaded from the
    # mirror bucket via Azul, this value will be overridden with the requested
    # file's actual content type via a query parameter in the signed URL.
    #
    # Files mirrored prior to this change may erroneously specify a different
    # value in the `Content-Type` metadata. We haven't found an efficient way to
    # update the content type of an existing object without copying its data.
    #
    _file_object_content_type = 'application/octet-stream'

    @cached_property
    def _repository_plugin(self) -> RepositoryPlugin:
        return RepositoryPlugin.load(self.catalog).create(self.catalog)

    def mirror_file(self, file: File):
        """
        Upload the file in a single request. For larger files, use
        :meth:`begin_mirroring_file` instead.
        """
        file_content = self._download(file)
        self._storage.put_object(object_key=self._file_object_key(file),
                                 data=file_content,
                                 content_type=self._file_object_content_type,
                                 overwrite=False)
        hasher = get_resumable_hasher(file.digest.type)
        hasher.update(file_content)
        self._verify_digest(file, hasher)
        self._put_info(file)

    def begin_mirroring_file(self, file: File) -> str:
        """
        Initiate a multipart upload of the file's content and return the upload
        ID.
        """
        object_key = self._file_object_key(file)
        content_type = self._file_object_content_type
        upload_id = self._storage.create_multipart_upload(object_key=object_key,
                                                          content_type=content_type)
        return upload_id

    def mirror_file_part(self,
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
        object_key = self._file_object_key(file)
        content = self._download(file, part)
        hasher.update(content)
        return self._storage.upload_multipart_part(object_key=object_key,
                                                   upload_id=upload_id,
                                                   part_number=part.index + 1,
                                                   buffer=content)

    def finish_mirroring_file(self,
                              *,
                              file: File,
                              upload_id: str,
                              etags: Sequence[str],
                              hasher: Hasher
                              ):
        """
        Complete a multipart upload begun with :meth:`begin_mirroring_file`.
        """
        object_key = self._file_object_key(file)
        self._storage.complete_multipart_upload(object_key=object_key,
                                                upload_id=upload_id,
                                                etags=etags,
                                                overwrite=False)
        self._verify_digest(file, hasher)
        self._get_info(file)
        self._put_info(file)

    def _info(self, file: File) -> JSON:
        return {
            'content-type': file.content_type,
            '$schema': str(self._schema_url_func(schema_name='info', version=1))
        }

    def _put_info(self, file: File):
        object_key = self._info_object_key(file)
        info = self._info(file)
        self._storage.put_object(object_key=object_key,
                                 data=json.dumps(info).encode(),
                                 content_type='application/json')

    def _repository_url(self, file: File) -> furl:
        assert config.is_tdr_enabled(self.catalog), R(
            'Only TDR catalogs are supported', self.catalog)
        assert file.drs_uri is not None, R(
            'File cannot be downloaded', file)
        drs = self._repository_plugin.drs_client(authentication=None)
        access = drs.get_object(file.drs_uri, AccessMethod.gs)
        assert access.method is AccessMethod.https, access
        return furl(access.url)

    def _download(self, file: File, part: FilePart | None = None) -> bytes:
        url = self._repository_url(file)
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
        response = self._http_client.request('GET', str(url), headers=headers)
        if response.status == expected_status:
            actual_size = len(response.data)
            log.info('Downloaded %d bytes in %.3fs from file %r',
                     actual_size, time.time() - start, file)
            assert actual_size == size, R(f'Expected {size} bytes, got {actual_size}')
            return response.data
        else:
            raise RuntimeError('Unexpected response from repository', response.status)

    def _verify_digest(self, file: File, hasher: Hasher):
        expected_digest = file.digest
        actual_digest_value = hasher.hexdigest()
        assert expected_digest.value == actual_digest_value, R(
            'File digest value does not match its contents',
            expected_digest, file)
