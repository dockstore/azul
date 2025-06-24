from collections import (
    defaultdict,
)
from dataclasses import (
    dataclass,
    replace,
)
from enum import (
    auto,
)
import logging
from typing import (
    Self,
)

from azul import (
    CatalogName,
    cached_property,
    json_mapping,
)
from azul.azulclient import (
    AzulClient,
)
from azul.indexer import (
    BundlePartition,
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
    Action,
    SQSFifoMessage,
    SQSMessage,
)
from azul.types import (
    JSON,
    json_int,
    json_str,
)

log = logging.getLogger(__name__)


class IndexAction(Action):
    reindex = auto()
    add = auto()
    delete = auto()


class IndexQueueService:

    @cached_property
    def index_service(self) -> IndexService:
        return IndexService()

    @cached_property
    def client(self) -> AzulClient:
        return AzulClient()

    def queue_message(self, message: SQSMessage, *, retry: bool):
        queue = self.client.notifications_queue(retry=retry)
        queue.send_message(**message.to_entry())
        log.info('Queued notification message %r', message)

    def contribute(self, action: IndexAction, message: JSON):
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
                self.queue_message(message, retry=True)
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

    def aggregate(self, tallies: list['DocumentTally'], *, retry: bool):
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
