from collections import (
    defaultdict,
)
from collections.abc import (
    Iterable,
)
from dataclasses import (
    dataclass,
    replace,
)
import http
import json
import logging
from typing import (
    Self,
)
import uuid

import chalice
from chalice.app import (
    SQSRecord,
    UnauthorizedError,
)

from azul import (
    CatalogName,
    R,
    cached_property,
    config,
    json_mapping,
)
from azul.azulclient import (
    AzulClient,
    IndexAction,
)
from azul.hmac import (
    HMACAuthentication,
)
from azul.indexer import (
    BundlePartition,
)
from azul.indexer.action_controller import (
    ActionController,
)
from azul.indexer.document import (
    Contribution,
    EntityReference,
    Replica,
)
from azul.indexer.index_service import (
    CataloguedEntityReference,
    IndexService,
)
from azul.queues import (
    SQSFifoMessage,
    SQSMessage,
)
from azul.types import (
    JSON,
    json_int,
    json_str,
)

log = logging.getLogger(__name__)


class IndexController(ActionController[IndexAction]):

    @cached_property
    def index_service(self) -> IndexService:
        return IndexService()

    @cached_property
    def client(self) -> AzulClient:
        return AzulClient()

    def handle_notification(self, catalog: CatalogName, action: str):
        request = self.current_request
        if isinstance(request.authentication, HMACAuthentication):
            assert request.authentication.identity() is not None
            try:
                config.Catalog.validate_name(catalog)
            except AssertionError as e:
                if R.caused(e):
                    raise R.propagate(e, chalice.BadRequestError)
            notification = request.json_body
            log.info('Received notification %r for catalog %r', notification, catalog)
            self._validate_notification(notification)
            message = self.client.index_bundle_message(self._load_action(action),
                                                       catalog,
                                                       notification['bundle_fqid'],
                                                       BundlePartition.root)
            self._queue_message(message, retry=False)
            return chalice.app.Response(body='', status_code=http.HTTPStatus.ACCEPTED)
        else:
            raise UnauthorizedError()

    def _queue_message(self, message: SQSMessage, *, retry: bool):
        queue = self.client.notifications_queue(retry=retry)
        queue.send_message(**message.to_entry())
        log.info('Queued notification message %r', message)

    def _validate_notification(self, notification):
        try:
            bundle_fqid = notification['bundle_fqid']
        except KeyError:
            raise chalice.BadRequestError('Missing notification entry: bundle_fqid')

        try:
            bundle_uuid = bundle_fqid['uuid']
        except KeyError:
            raise chalice.BadRequestError('Missing notification entry: bundle_fqid.uuid')

        try:
            bundle_version = bundle_fqid['version']
        except KeyError:
            raise chalice.BadRequestError('Missing notification entry: bundle_fqid.version')

        if not isinstance(bundle_uuid, str):
            raise chalice.BadRequestError(f'Invalid type: uuid: {type(bundle_uuid)} (should be str)')

        if not isinstance(bundle_version, str):
            raise chalice.BadRequestError(f'Invalid type: version: {type(bundle_version)} (should be str)')

        if bundle_uuid.lower() != str(uuid.UUID(bundle_uuid)).lower():
            raise chalice.BadRequestError(f'Invalid syntax: {bundle_uuid} (should be a UUID)')

        if not bundle_version:
            raise chalice.BadRequestError('Invalid syntax: bundle_version can not be empty')

    def contribute(self, event: Iterable[SQSRecord], *, retry=False):
        self._handle_events(event, self._contribute)

    def _contribute(self, action: IndexAction, message: JSON):
        if action is IndexAction.reindex:
            self.client.remote_reindex_partition(message)
        else:
            catalog = json_str(message['catalog'])
            assert catalog is not None
            delete = action is IndexAction.delete
            bundle_fqid = json_mapping(message['bundle_fqid'])
            bundle_partition = json_mapping(message['bundle_partition'])
            bundle_partition = BundlePartition.from_json(bundle_partition)
            contributions, replicas = self.transform(catalog,
                                                     bundle_fqid,
                                                     bundle_partition,
                                                     delete=delete)
            log.info('Writing %i contributions to index.', len(contributions))
            tallies = self.index_service.contribute(catalog, contributions)
            tallies = [DocumentTally.for_entity(catalog, entity, num_contributions)
                       for entity, num_contributions in tallies.items()]

            if replicas:
                if delete:
                    # FIXME: Replica index does not support deletions
                    #        https://github.com/DataBiosphere/azul/issues/5846
                    log.warning('Deletion of replicas is not supported')
                else:
                    log.info('Writing %i replicas to index.', len(replicas))
                    num_written = self.index_service.replicate(catalog, replicas)
                    log.info('Successfully wrote %i replicas', num_written)
            else:
                log.info('No replicas to write.')

            log.info('Queueing %i entities for aggregating a total of %i contributions.',
                     len(tallies), sum(tally.num_contributions for tally in tallies))
            messages = (tally.to_message() for tally in tallies)
            self.client.queue_tallies(messages)

    def transform(self,
                  catalog: CatalogName,
                  bundle_fqid: JSON,
                  bundle_partition: BundlePartition,
                  *,
                  delete: bool
                  ) -> tuple[list[Contribution], list[Replica]]:
        """
        Transform the metadata in the bundle referenced by the given
        notification into a list of contributions to documents, each document
        representing one metadata entity in the index. Replicas of the original,
        untransformed metadata are returned as well.
        """
        service = self.index_service
        bundle = service.fetch_bundle(catalog, bundle_fqid)
        results = service.transform(catalog, bundle, bundle_partition, delete=delete)
        if isinstance(results, list):
            action = IndexAction.delete if delete else IndexAction.add
            for bundle_partition in results:
                assert isinstance(bundle_partition, BundlePartition)
                # There's a good chance that the partition will also fail in
                # the non-retry Lambda function so we'll go straight to retry.
                message = self.client.index_bundle_message(action,
                                                           catalog,
                                                           bundle_fqid,
                                                           bundle_partition)
                self._queue_message(message, retry=True)
            return [], []
        elif isinstance(results, tuple):
            return results
        else:
            assert False, results

    #: The number of failed attempts before a tally is referred as a batch of 1.
    #: Note that the retry lambda does first attempts, too, namely on re-fed and
    #: deferred tallies.
    #
    num_batched_aggregation_attempts = 3

    def aggregate(self, event: Iterable[SQSRecord], *, retry=False):
        # Consolidate multiple tallies for the same entity and process entities
        # with only one message. Because SQS FIFO queues try to put as many
        # messages from the same message group in a reception batch, a single
        # message per group may indicate that that message is the last one in
        # the group. Inversely, multiple messages per group in a batch are a
        # likely indicator for the presence of even more queued messages in
        # that group. The more bundle contributions we defer, the higher the
        # amortized savings on aggregation become. Aggregating bundle
        # contributions is a costly operation for any entity with many
        # contributions e.g., a large project.
        #
        tallies = []
        for record in event:
            body = json.loads(record.body)
            attributes = record.to_dict()['attributes']
            attempts = int(attributes['ApproximateReceiveCount'])
            tally = DocumentTally.from_json(json=body, attempts=attempts)
            log.info('Attempt %i of handling %i contribution(s) for entity %s',
                     tally.attempts, tally.num_contributions, tally.entity)
            tallies.append(tally)
        try:
            self._aggregate(tallies, retry=retry)
        except BaseException:
            # Note that another problematic outcome is for the Lambda invocation
            # to time out, in which case this log message will not be written.
            log.warning('Failed to aggregate tallies: %r', tallies, exc_info=True)
            raise

    def _aggregate(self, tallies: list['DocumentTally'], *, retry: bool):
        tallies_by_entity: dict[CataloguedEntityReference, list[DocumentTally]] = defaultdict(list)
        for tally in tallies:
            tallies_by_entity[tally.entity].append(tally)
        deferrals, referrals = [], []
        for tallies in tallies_by_entity.values():
            if len(tallies) == 1:
                referrals.append(tallies[0])
            elif len(tallies) > 1:
                deferrals.append(tallies[0].consolidate(tallies[1:]))
            else:
                assert False
        if referrals:
            for i, tally in enumerate(referrals):
                if tally.attempts > self.num_batched_aggregation_attempts:
                    log.info('Only aggregating problematic entity %s, deferring all others',
                             tally.entity)
                    referrals.pop(i)
                    deferrals.extend(referrals)
                    referrals = [tally]
                    break

            log.info('Referring %i tallies', len(referrals))
            tally_by_entity = {}
            for tally in referrals:
                log.info('Aggregating %i contribution(s) to entity %s',
                         tally.num_contributions, tally.entity)
                tally_by_entity[tally.entity] = tally.num_contributions

            self.index_service.aggregate(tally_by_entity)

            for tally in referrals:
                log.info('Successfully aggregated %i contribution(s) to entity %s',
                         tally.num_contributions, tally.entity)
            log.info('Successfully referred %i tallies', len(referrals))
        if deferrals:
            log.info('Deferring %i tallies', len(deferrals))
            for tally in deferrals:
                log.info('Deferring aggregation of %i contribution(s) to entity %s',
                         tally.num_contributions, tally.entity)
            messages = (tally.to_message() for tally in deferrals)
            # Hopefully this is more or less atomic. If we crash below here,
            # tallies will be inflated because some or all deferrals have
            # been sent and the original tallies will be returned.
            self.client.queue_tallies(messages, retry=retry)


@dataclass(frozen=True)
class DocumentTally:
    """
    Tracks the number of bundle contributions to a particular metadata entity.

    Each instance represents a message in the document queue.
    """
    entity: CataloguedEntityReference
    num_contributions: int
    attempts: int

    @classmethod
    def for_entity(cls,
                   catalog: CatalogName,
                   entity: EntityReference,
                   num_contributions: int) -> Self:
        return cls(entity=CataloguedEntityReference(catalog=catalog,
                                                    entity_type=entity.entity_type,
                                                    entity_id=entity.entity_id),
                   num_contributions=num_contributions,
                   attempts=0)

    @classmethod
    def from_json(cls, json: JSON, attempts: int) -> Self:
        return cls(entity=CataloguedEntityReference(catalog=json_str(json['catalog']),
                                                    entity_type=json_str(json['entity_type']),
                                                    entity_id=json_str(json['entity_id'])),
                   num_contributions=json_int(json['num_contributions']),
                   attempts=attempts)

    def to_json(self) -> JSON:
        return {
            'catalog': self.entity.catalog,
            'entity_type': self.entity.entity_type,
            'entity_id': self.entity.entity_id,
            'num_contributions': self.num_contributions
        }

    def to_message(self) -> SQSFifoMessage:
        return SQSFifoMessage(body=self.to_json(),
                              group_id=str(self.entity))

    def consolidate(self, others: list['DocumentTally']) -> Self:
        assert all(
            self.entity == other.entity
            for other in others
        )
        return replace(self, num_contributions=sum((other.num_contributions for other in others),
                                                   self.num_contributions))
