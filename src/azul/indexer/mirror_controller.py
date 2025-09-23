from functools import (
    partial,
)
import logging
from typing import (
    Any,
    Iterable,
    Sequence,
    cast,
)

import chalice
from chalice.app import (
    SQSRecord,
)

from azul import (
    CatalogName,
    R,
    cache,
    cached_property,
    config,
)
from azul.azulclient import (
    AzulClient,
    MirrorAction,
)
from azul.chalice import (
    LambdaMetric,
)
from azul.deployment import (
    aws,
)
from azul.digests import (
    Hasher,
    get_resumable_hasher,
    hasher_from_str,
    hasher_to_str,
)
from azul.indexer import (
    SourceRef,
)
from azul.indexer.action_controller import (
    ActionController,
)
from azul.indexer.mirror_service import (
    FilePart,
    MirrorService,
)
from azul.plugins import (
    File,
    RepositoryPlugin,
)
from azul.queues import (
    SQSFifoMessage,
    SQSMessage,
)
from azul.schemas import (
    SchemaController,
)
from azul.types import (
    JSON,
    json_element_strings,
    json_mapping,
    json_str,
)

log = logging.getLogger(__name__)


class MirrorController(ActionController[MirrorAction], SchemaController):

    @cached_property
    def client(self) -> AzulClient:
        return AzulClient()

    @property
    def actions_are_fifo(self) -> bool:
        return True

    @cache
    def service(self, catalog: CatalogName) -> MirrorService:
        schema_url_func = partial(self.schema_url, facility='mirror')
        return MirrorService(catalog=catalog, schema_url_func=schema_url_func)

    def repository_plugin(self, catalog: CatalogName) -> RepositoryPlugin:
        return self.client.repository_plugin(catalog)

    def handlers(self) -> dict[str, Any]:
        if config.enable_mirroring:
            @self.app.metric_alarm(metric=LambdaMetric.errors,
                                   threshold=int(config.mirroring_concurrency * 2 / 3),
                                   period=5 * 60)
            @self.app.metric_alarm(metric=LambdaMetric.throttles,
                                   threshold=int(96000 / config.mirroring_concurrency),
                                   period=5 * 60)
            @self.app.on_sqs_message(queue=config.mirror_queue.name,
                                     batch_size=1)
            def mirror(event: chalice.app.SQSEvent):
                self.mirror(event)

        return super().handlers() | locals()

    def mirror(self, event: Iterable[SQSRecord]):
        self._handle_events(event, self._mirror)

    def _mirror(self, action: MirrorAction, message: JSON):
        if action is MirrorAction.mirror_source:
            self.mirror_source(json_str(message['catalog']),
                               json_mapping(message['source']))
        elif action is MirrorAction.mirror_partition:
            self.mirror_partition(json_str(message['catalog']),
                                  json_mapping(message['source']),
                                  json_str(message['prefix']))
        elif action is MirrorAction.mirror_file:
            self.mirror_file(json_str(message['catalog']),
                             json_mapping(message['file']))
        elif action is MirrorAction.mirror_part:
            self.mirror_file_part(json_str(message['catalog']),
                                  json_mapping(message['file']),
                                  json_mapping(message['part']),
                                  json_str(message['upload_id']),
                                  list(json_element_strings(message['etags'])),
                                  json_str(message['hasher']))
        elif action is MirrorAction.finalize_file:
            self.finalize_file(json_str(message['catalog']),
                               json_mapping(message['file']),
                               json_str(message['upload_id']),
                               list(json_element_strings(message['etags'])),
                               json_str(message['hasher']))
        else:
            assert False, action

    def mirror_source(self, catalog: CatalogName, source_json: JSON):
        plugin = self.repository_plugin(catalog)
        source = plugin.source_ref_cls.from_json(source_json)
        # The desired partition size depends on the maximum number of messages
        # we can send in one Lambda invocation, because queueing the individual
        # mirror_file messages turns out to dominate the running time of
        # handling a mirror_source message.
        partition_size = int(
            aws.sqs_fifo_rate_limit  # max. # of SendMessage calls per second
            * self.client.queues.batch_size  # number of messages per call
            * config.mirror_lambda_timeout  # max. duration of the invocation
            / config.mirroring_concurrency  # number of concurrent invocations
            / 2  # safety margin
        )
        source = plugin.partition_source_for_mirroring(catalog, source, partition_size)
        prefix = source.spec.prefix
        log.info('Queueing %d partitions of source %r in catalog %r',
                 prefix.num_partitions, str(source.spec), catalog)

        def message(partition: str) -> SQSMessage:
            log.debug('Queueing partition %r', partition)
            return self.mirror_partition_message(catalog, source, partition)

        messages = map(message, prefix.partition_prefixes())
        self.client.queue_mirror_messages(messages)

    def mirror_partition(self,
                         catalog: CatalogName,
                         source_json: JSON,
                         prefix: str
                         ):
        plugin = self.repository_plugin(catalog)
        source = plugin.source_ref_cls.from_json(source_json)
        files = plugin.list_files(source, prefix)

        def messages() -> Iterable[SQSMessage]:
            for file in files:
                log.debug('Queueing file %r', file)
                yield self.mirror_file_message(catalog, source, file)

        self.client.queue_mirror_messages(messages())
        log.info('Queued %d files in partition %r of source %r in catalog %r',
                 len(files), prefix, str(source), catalog)

    def mirror_file(self,
                    catalog: CatalogName,
                    file_json: JSON
                    ):
        file = self.load_file(catalog, file_json)
        assert file.size is not None, R('File size unknown', file)

        file_is_large = file.size > 1.5 * 1024 ** 3
        deployment_is_stable = (config.deployment.is_stable
                                and not config.deployment.is_unit_test
                                and catalog not in config.integration_test_catalogs)

        service = self.service(catalog)
        if file_is_large and not deployment_is_stable:
            log.info('Not mirroring file to save cost: %r', file)
        elif service.info_exists(file):
            log.info('File is already mirrored, skipping upload: %r', file)
        elif service.file_exists(file):
            assert False, R('File object is already present', file)
        else:
            part_size = FilePart.default_size
            if file.size <= part_size:
                log.info('Mirroring file via standard upload: %r', file)
                service.mirror_file(file)
                log.info('Successfully mirrored file via standard upload: %r', file)
            else:
                log.info('Mirroring file via multi-part upload: %r', file)
                hasher = get_resumable_hasher(file.digest.type)
                upload_id = service.begin_mirroring_file(file)
                first_part = FilePart.first(file, part_size)
                log.info('Uploading part #%d of file %r', first_part.index, file)
                etag = service.mirror_file_part(file,
                                                first_part,
                                                upload_id,
                                                hasher)
                next_part = first_part.next(file)
                assert next_part is not None
                log.info('Queueing part #%d of file %r', next_part.index, file)
                message = self.mirror_part_message(catalog,
                                                   file,
                                                   next_part,
                                                   upload_id,
                                                   [etag],
                                                   hasher)
                self.client.queue_mirror_messages([message])

    def mirror_file_part(self,
                         catalog: CatalogName,
                         file_json: JSON,
                         part_json: JSON,
                         upload_id: str,
                         etags: Iterable[str],
                         hasher_data: str
                         ):
        file = self.load_file(catalog, file_json)
        part = FilePart.from_json(part_json)
        hasher = hasher_from_str(hasher_data)
        log.info('Uploading part #%d of file %r', part.index, file)
        service = self.service(catalog)
        etag = service.mirror_file_part(file, part, upload_id, hasher)
        etags = [*etags, etag]
        next_part = part.next(file)
        if next_part is None:
            log.info('File fully uploaded in %d parts: %r', len(etags), file)
            message = self.finalize_file_message(catalog,
                                                 file,
                                                 upload_id,
                                                 etags,
                                                 hasher)
        else:
            log.info('Queueing part #%d of file %r', next_part.index, file)
            message = self.mirror_part_message(catalog,
                                               file,
                                               next_part,
                                               upload_id,
                                               etags,
                                               hasher)
        self.client.queue_mirror_messages([message])

    def finalize_file(self,
                      catalog: CatalogName,
                      file_json: JSON,
                      upload_id: str,
                      etags: Sequence[str],
                      hasher_data: str
                      ):
        file = self.load_file(catalog, file_json)
        assert len(etags) > 0
        hasher = hasher_from_str(hasher_data)
        service = self.service(catalog)
        service.finish_mirroring_file(file=file,
                                      upload_id=upload_id,
                                      etags=etags,
                                      hasher=hasher)
        log.info('Successfully mirrored file via multi-part upload: %r', file)

    def load_file(self, catalog: CatalogName, file: JSON) -> File:
        return self.client.metadata_plugin(catalog).file_class.from_json(file)

    def mirror_partition_message(self,
                                 catalog: CatalogName,
                                 source: SourceRef,
                                 prefix: str
                                 ) -> SQSFifoMessage:
        return SQSFifoMessage(
            body={
                'action': MirrorAction.mirror_partition.to_json(),
                'catalog': catalog,
                'source': cast(JSON, source.to_json()),
                'prefix': prefix
            },
            group_id=f'{source.id}:{prefix}'
        )

    def mirror_file_message(self,
                            catalog: CatalogName,
                            source: SourceRef,
                            file: File,
                            ) -> SQSFifoMessage:
        return SQSFifoMessage(
            body={
                'action': MirrorAction.mirror_file.to_json(),
                'catalog': catalog,
                'source': cast(JSON, source.to_json()),
                'file': file.to_json()
            },
            group_id=file.digest.value
        )

    def mirror_part_message(self,
                            catalog: CatalogName,
                            file: File,
                            part: FilePart,
                            upload_id: str,
                            etags: Sequence[str],
                            hasher: Hasher
                            ) -> SQSFifoMessage:
        return SQSFifoMessage(
            body={
                'catalog': catalog,
                'file': file.to_json(),
                'upload_id': upload_id,
                'action': MirrorAction.mirror_part.to_json(),
                'part': part.to_json(),
                'etags': etags,
                'hasher': hasher_to_str(hasher)
            },
            group_id=file.digest.value
        )

    def finalize_file_message(self,
                              catalog: CatalogName,
                              file: File,
                              upload_id: str,
                              etags: Sequence[str],
                              hasher: Hasher
                              ) -> SQSFifoMessage:
        return SQSFifoMessage(
            body={
                'catalog': catalog,
                'file': file.to_json(),
                'upload_id': upload_id,
                'action': MirrorAction.finalize_file.to_json(),
                'etags': etags,
                'hasher': hasher_to_str(hasher)
            },
            group_id=file.digest.value
        )
