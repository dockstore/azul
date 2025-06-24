from collections.abc import (
    Iterable,
)
import http
import json
import logging
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
from azul.indexer.index_queue_service import (
    DocumentTally,
    IndexQueueService,
)

log = logging.getLogger(__name__)


class IndexController(ActionController[IndexAction]):

    @cached_property
    def index_queue_service(self) -> IndexQueueService:
        return IndexQueueService()

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
            self.index_queue_service.queue_message(message, retry=False)
            return chalice.app.Response(body='', status_code=http.HTTPStatus.ACCEPTED)
        else:
            raise UnauthorizedError()

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
        self._handle_events(event, self.index_queue_service.contribute)

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
            self.index_queue_service.aggregate(tallies, retry=retry)
        except BaseException:
            # Note that another problematic outcome is for the Lambda invocation
            # to time out, in which case this log message will not be written.
            log.warning('Failed to aggregate tallies: %r', tallies, exc_info=True)
            raise
