from collections import (
    defaultdict,
)
from collections.abc import (
    Iterable,
)
from concurrent.futures import (
    Future,
    ThreadPoolExecutor,
)
from enum import (
    Enum,
    auto,
)
from functools import (
    partial,
)
from itertools import (
    groupby,
)
import logging
from pprint import (
    PrettyPrinter,
)
from typing import (
    Self,
    TYPE_CHECKING,
    cast,
)
import uuid

import attrs
import requests
from urllib3 import (
    HTTPResponse,
)
from urllib3.exceptions import (
    HTTPError,
)

from azul import (
    CatalogName,
    R,
    cached_property,
    config,
)
from azul.deployment import (
    aws,
)
from azul.es import (
    ESClientFactory,
)
from azul.hmac import (
    SignatureHelper,
)
from azul.http import (
    HasCachedHttpClient,
)
from azul.indexer import (
    SourceRef,
    SourcedBundleFQID,
)
from azul.indexer.index_service import (
    IndexService,
)
from azul.json import (
    Serializable,
)
from azul.plugins import (
    MetadataPlugin,
    RepositoryPlugin,
)
from azul.queues import (
    Queues,
    SQSFifoMessage,
    SQSMessage,
)
from azul.types import (
    AnyJSON,
    JSON,
    json_mapping,
)

if TYPE_CHECKING:
    from mypy_boto3_sqs.service_resource import (
        Queue,
    )

log = logging.getLogger(__name__)


class Action(Serializable, Enum):

    @classmethod
    def from_json(cls, action: AnyJSON) -> Self:
        assert isinstance(action, str), R('Action is not a string', type(action))
        try:
            return cls[action]
        except KeyError:
            assert False, R('Invalid action', action)

    def to_json(self) -> str:
        return self.name


class IndexAction(Action):
    reindex = auto()
    add = auto()
    delete = auto()


class MirrorAction(Action):
    mirror_source = auto()
    mirror_partition = auto()
    mirror_file = auto()
    mirror_part = auto()
    finalize_file = auto()


@attrs.frozen(kw_only=True)
class AzulClient(SignatureHelper, HasCachedHttpClient):
    num_workers: int = 16

    def repository_plugin(self, catalog: CatalogName) -> RepositoryPlugin:
        return self.index_service.repository_plugin(catalog)

    def metadata_plugin(self, catalog: CatalogName) -> MetadataPlugin:
        return self.index_service.metadata_plugin(catalog)

    def notification(self, bundle_fqid: SourcedBundleFQID) -> JSON:
        """
        Generate an indexer notification for the given bundle.
        """
        # Organic notifications sent by DSS have a different structure,
        # but since DSS is end-of-life these synthetic notifications are now the
        # only variant that would ever occur in the wild.
        return {
            'transaction_id': str(uuid.uuid4()),
            'bundle_fqid': bundle_fqid.to_json()
        }

    def index_bundle_message(self,
                             catalog: CatalogName,
                             notification: JSON,
                             action: IndexAction,
                             ) -> SQSMessage:
        return SQSMessage(
            body={
                'action': action.to_json(),
                'notification': notification,
                'catalog': catalog
            }
        )

    def bundle_message(self,
                       catalog: CatalogName,
                       bundle_fqid: SourcedBundleFQID
                       ) -> SQSMessage:
        return self.index_bundle_message(catalog, self.notification(bundle_fqid), IndexAction.add)

    def index_partition_message(self,
                                catalog: CatalogName,
                                source: SourceRef,
                                prefix: str
                                ) -> SQSMessage:
        return SQSMessage(
            body={
                'action': IndexAction.reindex.to_json(),
                'catalog': catalog,
                'source': cast(JSON, source.to_json()),
                'prefix': prefix
            }
        )

    def mirror_source_message(self,
                              catalog: CatalogName,
                              source: SourceRef
                              ) -> SQSFifoMessage:
        return SQSFifoMessage(
            body={
                'action': MirrorAction.mirror_source.to_json(),
                'catalog': catalog,
                'source': cast(JSON, source.to_json()),
            },
            group_id=source.id
        )

    def local_reindex(self, catalog: CatalogName, prefix: str) -> int:
        notifications = [
            self.notification(bundle_fqid)
            for source in self.catalog_sources(catalog)
            for bundle_fqid in self.list_bundles(catalog, source, prefix)
        ]
        self.index(catalog, notifications)
        return len(notifications)

    def index(self,
              catalog: CatalogName,
              notifications: Iterable[JSON],
              delete: bool = False
              ):
        errors = defaultdict[int, int](int)
        missing = []
        indexed = 0
        total = 0
        path = (catalog, 'delete' if delete else 'add')
        indexer_url = config.indexer_endpoint.set(path=path)

        def attempt(notification: JSON,
                    i: int
                    ) -> tuple[JSON, None | Future | HTTPResponse | HTTPError]:
            log_args = (indexer_url, notification, i)
            log.info('Notifying %s about %s, attempt %i.',
                     *log_args)
            # We want to send the request with urllib3 directly but HMAC
            # signing is only available for Requests, so we need to prepare a
            # request, sign it and then unpack it again before calling urllib3.
            request = requests.Request('POST', str(indexer_url), json=notification)
            request = request.prepare()
            self.sign(request)
            try:
                result = self._http_client.request(url=request.url,
                                                   method=request.method,
                                                   headers=request.headers,
                                                   body=request.body)
            except HTTPError as e:
                result = e

            if isinstance(result, HTTPResponse) and result.status == 202:
                log.info('Success notifying %s about %s, attempt %i.',
                         *log_args)
                return notification, None
            else:
                assert isinstance(result, (HTTPResponse, HTTPError)), result
                if i < 3:
                    log.warning('Retrying to notify %s about %s, attempt %i, after error %s.',
                                *log_args, result)
                    return notification, tpe.submit(partial(attempt, notification, i + 1))
                else:
                    log.warning('Failed to notify %s about %s, attempt %i: after error %s.',
                                *log_args, result)
                    return notification, result

        def handle_future(future: Future) -> None:
            nonlocal indexed
            bundle_fqid, result = future.result()
            if result is None:
                indexed += 1
            elif isinstance(result, HTTPResponse):
                errors[result.status] += 1
                missing.append((notification, result.status))
            elif isinstance(result, Future):
                # The task scheduled a follow-on task, presumably a retry.
                # Follow that new task.
                handle_future(result)
            else:
                assert False

        with ThreadPoolExecutor(max_workers=self.num_workers,
                                thread_name_prefix='pool') as tpe:
            futures = []
            for notification in notifications:
                total += 1
                futures.append(tpe.submit(partial(attempt, notification, 0)))
            for future in futures:
                handle_future(future)

        printer = PrettyPrinter(compact=False)
        log.info('Sent notifications for %i of %i bundles for catalog %r.',
                 indexed, total, catalog)
        if errors:
            log.error('Number of errors by HTTP status code:\n%s',
                      printer.pformat(dict(errors)))
        if missing:
            log.error('Unsent notifications and their HTTP status code:\n%s',
                      printer.pformat(missing))
        if errors or missing:
            raise AzulClientNotificationError

    def catalog_sources(self, catalog: CatalogName) -> set[str]:
        return set(map(str, self.repository_plugin(catalog).sources))

    def sources_by_catalog(self, catalogs: Iterable[str]) -> dict[str, set[str]]:
        return {
            catalog: self.catalog_sources(catalog)
            for catalog in catalogs
        }

    def list_bundles(self,
                     catalog: CatalogName,
                     source: str | SourceRef,
                     prefix: str
                     ) -> list[SourcedBundleFQID]:
        plugin = self.repository_plugin(catalog)
        if isinstance(source, str):
            source = plugin.resolve_source(source)
        else:
            assert isinstance(source, SourceRef), source
        log.info('Listing bundles with prefix %r in source %r.', prefix, source)
        bundle_fqids = plugin.list_bundles(source, prefix)
        log.info('There are %i bundle(s) with prefix %r in source %r.',
                 len(bundle_fqids), prefix, source)
        return bundle_fqids

    def notifications_queue(self, *, retry: bool = False) -> 'Queue':
        name = config.notifications_queue.derive(retry=retry).name
        return aws.sqs_queue(name)

    def tallies_queue(self, *, retry: bool = False) -> 'Queue':
        name = config.tallies_queue.derive(retry=retry).name
        return aws.sqs_queue(name)

    def mirror_queue(self):
        name = config.mirror_queue.name
        return aws.sqs_queue(name)

    def remote_reindex(self,
                       catalog: CatalogName,
                       sources: set[str]):

        plugin = self.repository_plugin(catalog)
        for source_spec in sources:
            source_ref = plugin.resolve_source(source_spec)
            source_ref = plugin.partition_source_for_indexing(catalog, source_ref)

            def message(partition_prefix: str) -> SQSMessage:
                log.info('Remotely reindexing prefix %r of source_ref %r into catalog %r',
                         partition_prefix, str(source_ref.spec), catalog)
                return self.index_partition_message(catalog, source_ref, partition_prefix)

            messages = map(message, source_ref.spec.prefix.partition_prefixes())
            self.queue_notifications(messages)

    def remote_reindex_partition(self, message: JSON) -> None:
        catalog, prefix = message['catalog'], message['prefix']
        assert isinstance(catalog, str) and isinstance(prefix, str)
        source = json_mapping(message['source'])
        source = self.repository_plugin(catalog).source_ref_cls.from_json(source)
        bundle_fqids = self.list_bundles(catalog, source, prefix)
        # All AnVIL bundles and entities use the same version
        if not config.is_anvil_enabled(catalog):
            bundle_fqids = self.filter_obsolete_bundle_versions(bundle_fqids)
            log.info('After filtering obsolete versions, '
                     '%i bundles remain in prefix %r of source %r in catalog %r',
                     len(bundle_fqids), prefix, str(source.spec), catalog)
        messages = (
            self.bundle_message(catalog, bundle_fqid)
            for bundle_fqid in bundle_fqids
        )
        num_messages = self.queue_notifications(messages)
        log.info('Successfully queued %i notification(s) for prefix %s of '
                 'source %r', num_messages, prefix, source)

    def queue_notifications(self,
                            messages: Iterable[SQSMessage],
                            *,
                            retry: bool = False
                            ) -> int:
        queue = self.notifications_queue(retry=retry)
        return self.queues.send_messages(queue, messages)

    def queue_tallies(self,
                      messages: Iterable[SQSMessage],
                      *,
                      retry: bool = False
                      ) -> int:
        queue = self.tallies_queue(retry=retry)
        return self.queues.send_messages(queue, messages)

    def queue_mirror_messages(self, messages: Iterable[SQSMessage]) -> int:
        return self.queues.send_messages(self.mirror_queue(), messages)

    @classmethod
    def filter_obsolete_bundle_versions(cls,
                                        bundle_fqids: Iterable[SourcedBundleFQID]
                                        ) -> list[SourcedBundleFQID]:
        """
        Suppress obsolete bundle versions by only taking the latest version for
        each bundle UUID.
        >>> AzulClient.filter_obsolete_bundle_versions([])
        []
        >>> from azul.indexer import SimpleSourceSpec, SourceRef, Prefix
        >>> p = Prefix.parse('/2')
        >>> s = SourceRef(id='i', spec=SimpleSourceSpec(prefix=p, name='n'))
        >>> def b(u, v):
        ...     return SourcedBundleFQID(source=s, uuid=u, version=v)
        >>> AzulClient.filter_obsolete_bundle_versions([
        ...     b('c', '0'),
        ...     b('a', '1'),
        ...     b('b', '3')
        ... ]) # doctest: +NORMALIZE_WHITESPACE
        [SourcedBundleFQID(uuid='c',
                           version='0',
                           source=SourceRef(id='i',
                                            spec=SimpleSourceSpec(prefix=Prefix(common='',
                                                                                partition=2),
                                                                  name='n'))),
        SourcedBundleFQID(uuid='b',
                          version='3',
                          source=SourceRef(id='i',
                                           spec=SimpleSourceSpec(prefix=Prefix(common='',
                                                                               partition=2),
                                                                 name='n'))),
        SourcedBundleFQID(uuid='a',
                          version='1',
                          source=SourceRef(id='i',
                                           spec=SimpleSourceSpec(prefix=Prefix(common='',
                                                                               partition=2),
                                                                 name='n')))]
        >>> AzulClient.filter_obsolete_bundle_versions([
        ...     b('C', '0'), b('a', '1'), b('a', '0'),
        ...     b('a', '2'), b('b', '1'), b('c', '2')
        ... ]) # doctest: +NORMALIZE_WHITESPACE
        [SourcedBundleFQID(uuid='c',
                           version='2',
                           source=SourceRef(id='i',
                                            spec=SimpleSourceSpec(prefix=Prefix(common='',
                                                                                partition=2),
                                                                  name='n'))),
        SourcedBundleFQID(uuid='b',
                          version='1',
                          source=SourceRef(id='i',
                                           spec=SimpleSourceSpec(prefix=Prefix(common='',
                                                                               partition=2),
                                                                 name='n'))),
        SourcedBundleFQID(uuid='a',
                          version='2',
                          source=SourceRef(id='i',
                                           spec=SimpleSourceSpec(prefix=Prefix(common='',
                                                                               partition=2),
                                                                 name='n')))]
        >>> AzulClient.filter_obsolete_bundle_versions([
        ...     b('a', '0'), b('A', '1')
        ... ]) # doctest: +NORMALIZE_WHITESPACE
        [SourcedBundleFQID(uuid='A',
                           version='1',
                           source=SourceRef(id='i',
                                            spec=SimpleSourceSpec(prefix=Prefix(common='',
                                                                                partition=2),
                                                                  name='n')))]
        """

        # Sort lexicographically by source and FQID. I've observed the DSS
        # response to already be in this order
        def sort_key(fqid: SourcedBundleFQID):
            return (
                fqid.source,
                fqid.uuid.lower(),
                fqid.version.lower()
            )

        bundle_fqids = sorted(bundle_fqids, key=sort_key, reverse=True)

        # Group by source and bundle UUID
        def group_key(fqid: SourcedBundleFQID):
            return (
                fqid.source.id.lower(),
                fqid.uuid.lower()
            )

        groups = groupby(bundle_fqids, key=group_key)

        # Take the first item in each group. Because the oder is reversed, this
        # is the latest version
        bundle_fqids = [next(group) for _, group in groups]
        return bundle_fqids

    @cached_property
    def index_service(self) -> IndexService:
        return IndexService()

    def delete_all_indices(self, catalog: CatalogName):
        self.index_service.delete_indices(catalog)

    def create_all_indices(self, catalog: CatalogName):
        self.index_service.create_indices(catalog)

    def delete_bundle(self, catalog: CatalogName, bundle_uuid, bundle_version):
        log.info('Deleting bundle %r, version %r in catalog %r.',
                 bundle_uuid, bundle_version, catalog)
        notifications = [
            {
                # FIXME: delete_bundle script fails with KeyError: 'source'
                #        https://github.com/DataBiosphere/azul/issues/5105
                'bundle_fqid': {
                    'uuid': bundle_uuid,
                    'version': bundle_version
                }
            }
        ]
        self.index(catalog, notifications, delete=True)

    def deindex(self, catalog: CatalogName, sources: Iterable[str]):
        plugin = self.repository_plugin(catalog)
        source_ids = [plugin.resolve_source(s).id for s in sources]
        es_client = ESClientFactory.get()
        indices = ','.join(map(str, self.index_service.index_names(catalog)))
        query = {
            'query': {
                'bool': {
                    'should': [
                        {
                            'terms': {
                                # Aggregate documents
                                'sources.id.keyword': source_ids
                            }
                        },
                        {
                            'terms': {
                                # Contribution documents
                                'source.id.keyword': source_ids
                            }
                        }
                    ]
                }
            }
        }
        log.info('Deindexing sources %r from catalog %r', sources, catalog)
        log.debug('Using query: %r', query)
        response = es_client.delete_by_query(index=indices, body=query, slices='auto')
        if len(response['failures']) > 0:
            if response['version_conflicts'] > 0:
                log.error('Version conflicts encountered. Do not deindex while '
                          'indexing is occurring. The index may now be in an '
                          'inconsistent state.')
            raise RuntimeError('Failures during deletion', response['failures'])

    @cached_property
    def queues(self) -> Queues:
        return Queues()

    def reset_indexer(self,
                      catalogs: Iterable[CatalogName],
                      *,
                      purge_queues: bool,
                      delete_indices: bool,
                      create_indices: bool):
        """
        Reset the indexer, to a degree.

        :param catalogs: The catalogs to create and delete indices for.

        :param purge_queues: whether to purge the indexer queues at the
                             beginning. Note that purging the queues affects
                             all catalogs, not just the specified one.

        :param delete_indices: whether to delete the indexes before optionally
                               recreating them

        :param create_indices: whether to create the indexes at the end.
        """
        indexer_queues = self.queues.get_queues(config.indexer_work_queue_names)
        if purge_queues:
            log.info('Disabling lambdas ...')
            self.queues.manage_lambdas(indexer_queues, enable=False)
            log.info('Purging queues: %s', ', '.join(indexer_queues.keys()))
            self.queues.purge_queues_unsafely(indexer_queues)
        if delete_indices:
            log.info('Deleting indices ...')
            for catalog in catalogs:
                self.delete_all_indices(catalog)
        if purge_queues:
            log.info('Re-enabling lambdas ...')
            self.queues.manage_lambdas(indexer_queues, enable=True)
        if create_indices:
            log.info('Creating indices ...')
            for catalog in catalogs:
                self.create_all_indices(catalog)

    def wait_for_indexer(self):
        """
        Wait for indexer to begin processing notifications, then wait for work
        to finish.
        """
        # Indexing can still succeed after a transient stall. A stall's
        # transience cannot be proven until all lambdas and their respective
        # retries repeatedly time out, but this would result in an unreasonably
        # long wait time. Waiting for just one retry is sufficient to
        # accommodate the most probable scenarios for transient stalls.
        timeout = max(config.contribution_lambda_timeout(retry=True),
                      config.aggregation_lambda_timeout(retry=True))
        self.queues.wait_to_stabilize(config.indexer_work_queue_names,
                                      timeout,
                                      detect_stall=True)

    def wait_for_mirroring(self):
        self.queues.wait_to_stabilize(config.mirror_work_queue_names,
                                      config.mirror_lambda_timeout,
                                      detect_stall=False)

    def is_queue_empty(self, queue_name: str) -> bool:
        queues = self.queues.get_queues([queue_name])
        length, _ = self.queues.get_queue_lengths(queues)
        return length == 0

    def remote_mirror(self, catalog: CatalogName, sources: Iterable[SourceRef]):

        def message(source: SourceRef):
            log.info('Mirroring files in source %r from catalog %r',
                     str(source.spec), catalog)
            return self.mirror_source_message(catalog, source)

        messages = map(message, sources)
        self.queue_mirror_messages(messages)

    def _get_non_empty_fail_queues(self) -> set[str]:
        return {
            queue
            for queue in config.indexer_fail_queue_names
            if not self.is_queue_empty(queue)
        }

    _common_fail_queue_msg = (
        "If needed, empty the work queues via 'manage_queues.py purge_indexer'. "
        "Then run 'manage_queues.py dump --delete' for each fail queue listed: "
    )

    def require_no_failures_before(self):
        queues = self._get_non_empty_fail_queues()
        assert 0 == len(queues), R(
            'Cannot begin indexing because a previous operation failed: '
            'At least one fail queue is not empty. ' +
            self._common_fail_queue_msg,
            queues
        )

    def require_no_failures_after(self):
        queues = self._get_non_empty_fail_queues()
        assert 0 == len(queues), R(
            'At least one fail queue is not empty, indicating that there were '
            'persistent indexer failures. ' +
            self._common_fail_queue_msg,
            queues
        )


class AzulClientError(RuntimeError):
    pass


class AzulClientNotificationError(AzulClientError):

    def __init__(self) -> None:
        super().__init__('Some notifications could not be sent')
