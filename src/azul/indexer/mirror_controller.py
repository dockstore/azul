import logging
from typing import (
    Iterable,
    Sequence,
    cast,
)

import attrs
from chalice.app import (
    SQSRecord,
)

from azul import (
    CatalogName,
    R,
    cached_property,
    config,
)
from azul.azulclient import (
    AzulClient,
    MirrorAction,
)
from azul.chalice import (
    SchemaUrlFunc,
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
from azul.types import (
    JSON,
    json_element_strings,
    json_mapping,
    json_str,
)

log = logging.getLogger(__name__)


@attrs.frozen(auto_attribs=True, kw_only=True)
class MirrorController(ActionController[MirrorAction]):
    schema_url_func: SchemaUrlFunc

    @cached_property
    def client(self) -> AzulClient:
        return AzulClient()

    @cached_property
    def service(self) -> MirrorService:
        return MirrorService(schema_url_func=self.schema_url_func)

    def repository_plugin(self, catalog: CatalogName) -> RepositoryPlugin:
        return self.client.repository_plugin(catalog)

    def mirror(self, event: Iterable[SQSRecord]):
        self._handle_events(event, self._mirror)

    def _mirror(self, message: JSON):
        action = self._load_action(json_str(message['action']))
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
        source = plugin.partition_source_for_mirroring(catalog, source)

        def message(prefix: str) -> SQSMessage:
            log.info('Mirroring files in partition %r of source %r from catalog %r',
                     prefix, str(source.spec), catalog)
            return self.mirror_partition_message(catalog, source, prefix)

        messages = map(message, source.spec.prefix.partition_prefixes())
        self.client.queue_mirror_messages(messages)

    def mirror_partition(self, catalog: CatalogName, source_json: JSON, prefix: str):
        plugin = self.repository_plugin(catalog)
        source = plugin.source_ref_cls.from_json(source_json)
        already_mirrored = self.service.list_info_objects(catalog, prefix)

        def messages() -> Iterable[SQSMessage]:
            for file in plugin.list_files(source, prefix):
                info_key = self.service.info_object_key(file)
                if info_key in already_mirrored:
                    log.info('Not mirroring file %r because info object already exists at %r',
                             file.uuid, info_key)
                else:
                    log.info('Mirroring file %r', file.uuid)
                    yield self.mirror_file_message(catalog, source, file)

        self.client.queue_mirror_messages(messages())

    def mirror_file(self,
                    catalog: CatalogName,
                    file_json: JSON
                    ):
        file = self.load_file(catalog, file_json)
        assert file.size is not None, R('File size unknown', file)

        file_is_large = file.size > 10 * 1024 ** 2
        deployment_is_stable = (config.deployment.is_stable
                                and not config.deployment.is_unit_test
                                and catalog not in config.integration_test_catalogs)
        if file_is_large and not deployment_is_stable:
            log.info('Not mirroring file %r (%d bytes) to save cost',
                     file.uuid, file.size)
        else:
            # Ensure we test with multiple parts on lower deployments
            part_size = FilePart.default_size if deployment_is_stable else FilePart.min_size
            if file.size <= part_size:
                log.info('Mirroring file %r via standard upload', file.uuid)
                self.service.mirror_file(catalog, file)
                log.info('Successfully mirrored file %r via standard upload', file.uuid)
            else:
                log.info('Mirroring file %r via multi-part upload', file.uuid)
                _, digest_type = file.digest()
                hasher = get_resumable_hasher(digest_type)
                upload_id = self.service.begin_mirroring_file(catalog, file)
                first_part = FilePart.first(file, part_size)
                etag = self.service.mirror_file_part(catalog, file, first_part, upload_id, hasher)
                next_part = first_part.next(file)
                assert next_part is not None
                messages = [self.mirror_part_message(catalog, file, next_part, upload_id, [etag], hasher)]
                self.client.queue_mirror_messages(messages)

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
        etag = self.service.mirror_file_part(catalog, file, part, upload_id, hasher)
        etags = [*etags, etag]
        next_part = part.next(file)
        if next_part is None:
            log.info('File %r fully uploaded in %d parts', file.uuid, len(etags))
            message = self.finalize_file_message(catalog,
                                                 file,
                                                 upload_id,
                                                 etags,
                                                 hasher)
        else:
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
        self.service.finish_mirroring_file(catalog=catalog,
                                           file=file,
                                           upload_id=upload_id,
                                           etags=etags,
                                           hasher=hasher)
        log.info('Successfully mirrored file %r via multi-part upload', file.uuid)

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
            group_id=f'{source.id}:{file.uuid}'
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
            group_id=self.service.mirror_object_key(file)
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
            group_id=self.service.mirror_object_key(file)
        )
