from abc import (
    ABCMeta,
    abstractmethod,
)
from functools import (
    singledispatchmethod,
)
import logging
from typing import (
    Iterable,
)

import attrs

from azul import (
    CatalogName,
    R,
    cache,
    cached_property,
    config,
    json_mapping,
)
from azul.attrs import (
    serializable,
)
from azul.deployment import (
    aws,
)
from azul.digests import (
    Hasher,
    get_resumable_hasher,
    hasher_from_json,
    hasher_to_json,
)
from azul.functions import (
    compose,
)
from azul.indexer import (
    SourceConfig,
    SourceRef,
)
from azul.indexer.mirror_file_service import (
    FilePart,
    MirrorFileService,
    SchemaUrlFunc,
)
from azul.plugins import (
    File,
    RepositoryPlugin,
)
from azul.queues import (
    Action,
    Queues,
    SQSFifoMessage,
)
from azul.service.source_service import (
    SourceService,
)
from azul.types import (
    json_element_strings,
)

log = logging.getLogger(__name__)


@attrs.frozen(kw_only=True)
class MirrorAction(Action, metaclass=ABCMeta):
    catalog: CatalogName

    @property
    @abstractmethod
    def group_id(self) -> str:
        raise NotImplementedError

    def to_sqs(self) -> SQSFifoMessage:
        return SQSFifoMessage(body=json_mapping(self.to_json()),
                              group_id=self.group_id)


@attrs.frozen(kw_only=True)
class MirrorSourceAction(MirrorAction):
    source: SourceRef

    @property
    def group_id(self):
        return self.source.id


@attrs.frozen(kw_only=True)
class MirrorPartitionAction(MirrorSourceAction):
    prefix: str

    @property
    def group_id(self):
        return super().group_id + ':' + self.prefix


@attrs.frozen(kw_only=True)
class MirrorFileAction(MirrorPartitionAction):
    file: File

    @property
    def group_id(self):
        return self.file.digest.value


@attrs.frozen(kw_only=True)
class MultiPartUploadAction(MirrorFileAction):
    upload_id: str
    etags: list[str] = serializable(from_json=compose(list, json_element_strings),
                                    to_json=list)
    hasher: Hasher = serializable(from_json=hasher_from_json,
                                  to_json=hasher_to_json)


@attrs.frozen(kw_only=True)
class MirrorPartAction(MultiPartUploadAction):
    part: FilePart


class FinalizeFileAction(MultiPartUploadAction):
    pass


class BaseMirrorService:
    """
    Service for queuing mirroring work, e.g., sending action messages.
    """

    @cached_property
    def _queues(self) -> Queues:
        return Queues()

    def remote_mirror(self,
                      catalog: CatalogName,
                      sources: Iterable[tuple[SourceRef, SourceConfig]]
                      ):
        mirror_limit = config.catalogs[catalog].mirror_limit
        if mirror_limit is not None and mirror_limit < 0:
            log.info('Not mirroring any files in catalog %r because the file '
                     'size limit is negative', catalog)
        else:

            def messages():
                for source, cfg in sources:
                    if cfg.mirror:
                        log.info('Mirroring files in source %r from catalog %r',
                                 str(source.spec), catalog)
                        yield MirrorSourceAction(catalog=catalog, source=source)
                    else:
                        log.info('Not mirroring any files in source %r from catalog %r because '
                                 'mirroring is explicitly disabled',
                                 str(source.spec), catalog)

            self._queue_messages(messages())

    def mirror_file(self, catalog: CatalogName, source: SourceRef, file: File):
        self._queue_messages([MirrorFileAction(catalog=catalog,
                                               source=source,
                                               prefix='',
                                               file=file)])

    def _mirror_queue(self):
        name = config.mirror_queue.name
        return aws.sqs_queue(name)

    def _queue_messages(self, messages: Iterable[MirrorAction]) -> int:
        rate_limit = float(aws.sqs_fifo_rate_limit)
        if config.is_in_lambda:
            rate_limit /= config.mirroring_concurrency
        return self._queues.send_messages(self._mirror_queue(),
                                          map(MirrorAction.to_sqs, messages),
                                          rate_limit=rate_limit)


@attrs.frozen(kw_only=True, slots=False)
class MirrorService(BaseMirrorService):
    """
    Service that carries out mirroring work.
    """

    _schema_url_func: SchemaUrlFunc

    @cache
    def _file_service(self, catalog: CatalogName) -> MirrorFileService:
        return MirrorFileService(catalog=catalog,
                                 schema_url_func=self._schema_url_func)

    @cache
    def _repository_plugin(self, catalog: CatalogName) -> RepositoryPlugin:
        return RepositoryPlugin.load(catalog).create(catalog)

    @cached_property
    def _source_service(self) -> SourceService:
        return SourceService()

    def mirror(self, action: MirrorAction):
        self._queue_messages(self._mirror(action))

    @singledispatchmethod
    def _mirror(self, a: MirrorAction):
        raise NotImplementedError

    @_mirror.register
    def _(self, a: MirrorSourceAction) -> Iterable[MirrorAction]:
        assert a.source.id in self._list_public_source_ids(a.catalog), R(
            'Cannot mirror non-public source', a.source)
        # The desired partition size depends on the maximum number of messages
        # we can send in one Lambda invocation, because queueing the individual
        # mirror_file messages turns out to dominate the running time of
        # handling a mirror_source message.
        partition_size = int(
            aws.sqs_fifo_rate_limit  # max. # of SendMessage calls per second
            * Queues.batch_size  # number of messages per call
            * config.mirror_lambda_timeout  # max. duration of the invocation
            / config.mirroring_concurrency  # number of concurrent invocations
            / 2  # safety margin
        )
        plugin = self._repository_plugin(a.catalog)
        partitioned_source = plugin.partition_source_for_mirroring(a.catalog,
                                                                   a.source,
                                                                   partition_size)
        prefix = partitioned_source.prefix
        assert prefix is not None, partitioned_source
        log.info('Queueing %d partitions of source %r in catalog %r',
                 prefix.num_partitions, str(partitioned_source.spec), a.catalog)

        for partition in prefix.partition_prefixes():
            log.debug('Queueing partition %r', partition)
            yield MirrorPartitionAction(catalog=a.catalog,
                                        source=partitioned_source,
                                        prefix=partition)

    def _list_public_source_ids(self, catalog: CatalogName) -> set[str]:
        return self._source_service.list_source_ids(catalog, authentication=None)

    @_mirror.register
    def _(self, a: MirrorPartitionAction) -> Iterable[MirrorAction]:
        plugin = self._repository_plugin(a.catalog)
        files = plugin.list_files(a.source, a.prefix)
        max_size = config.catalogs[a.catalog].mirror_limit
        for file in files:
            assert file.size is not None, R('File size unknown', file)
            if max_size is not None and file.size > max_size:
                log.info('Not mirroring file to save cost: %r', file)
            else:
                log.debug('Queueing file %r', file)
                yield MirrorFileAction(catalog=a.catalog,
                                       source=a.source,
                                       prefix=a.prefix,
                                       file=file)
        log.info('Queued %d files in partition %r of source %r in catalog %r',
                 len(files), a.prefix, str(a.source), a.catalog)

    @_mirror.register
    def _(self, a: MirrorFileAction) -> Iterable[MirrorAction]:
        assert a.file.size is not None, R('File size unknown', a.file)
        service = self._file_service(a.catalog)
        if service.info_exists(a.file):
            log.info('File is already mirrored, skipping upload: %r', a.file)
        elif service.file_exists(a.file):
            assert False, R('File object is already present', a.file)
        else:
            part_size = FilePart.default_size
            if a.file.size <= part_size:
                log.info('Mirroring file via standard upload: %r', a.file)
                service.mirror_file(a.file)
                log.info('Successfully mirrored file via standard upload: %r', a.file)
            else:
                log.info('Mirroring file via multi-part upload: %r', a.file)
                hasher = get_resumable_hasher(a.file.digest.type)
                upload_id = service.begin_mirroring_file(a.file)
                first_part = FilePart.first(a.file, part_size)
                log.info('Uploading part #%d of file %r', first_part.index, a.file)
                etag = service.mirror_file_part(a.file, first_part, upload_id, hasher)
                next_part = first_part.next(a.file)
                assert next_part is not None
                log.info('Queueing part #%d of file %r', next_part.index, a.file)
                yield MirrorPartAction(catalog=a.catalog,
                                       source=a.source,
                                       prefix=a.prefix,
                                       file=a.file,
                                       part=next_part,
                                       upload_id=upload_id,
                                       etags=[etag],
                                       hasher=hasher)

    @_mirror.register
    def _(self, a: MirrorPartAction) -> Iterable[MirrorAction]:
        log.info('Uploading part #%d of file %r', a.part.index, a.file)
        service = self._file_service(a.catalog)
        # Hashers are mutable so we need to make a copy
        hasher = a.hasher.copy()
        etag = service.mirror_file_part(a.file, a.part, a.upload_id, hasher)
        # Same here: lists are mutable so a copy needs to be made
        etags = [*a.etags, etag]
        next_part = a.part.next(a.file)
        if next_part is None:
            log.info('File fully uploaded in %d parts: %r', len(etags), a.file)
            yield FinalizeFileAction(catalog=a.catalog,
                                     source=a.source,
                                     prefix=a.prefix,
                                     file=a.file,
                                     upload_id=a.upload_id,
                                     etags=etags,
                                     hasher=hasher)
        else:
            log.info('Queueing part #%d of file %r', next_part.index, a.file)
            yield MirrorPartAction(catalog=a.catalog,
                                   source=a.source,
                                   prefix=a.prefix,
                                   file=a.file,
                                   part=next_part,
                                   upload_id=a.upload_id,
                                   etags=etags,
                                   hasher=hasher)

    @_mirror.register
    def _(self, a: FinalizeFileAction) -> Iterable[MirrorAction]:
        assert len(a.etags) > 0
        service = self._file_service(a.catalog)
        service.finish_mirroring_file(file=a.file,
                                      upload_id=a.upload_id,
                                      etags=a.etags,
                                      hasher=a.hasher)
        log.info('Successfully mirrored file via multi-part upload: %r', a.file)
        return ()
