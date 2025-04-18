from collections.abc import (
    Mapping,
    Sequence,
)
import json
import logging
import time
from typing import (
    TYPE_CHECKING,
    cast,
)

from chalice import (
    BadRequestError,
    NotFoundError,
    TooManyRequestsError,
)

from azul import (
    CatalogName,
    R,
    cache,
    cached_property,
    config,
)
from azul.auth import (
    Authentication,
)
from azul.chalice import (
    ServiceUnavailableError,
)
from azul.http import (
    LimitedTimeoutException,
    TooManyRequestsException,
)
from azul.indexer.field import (
    FieldType,
    pass_thru_bool,
)
from azul.plugins import (
    RepositoryFileDownload,
    RepositoryPlugin,
)
from azul.service import (
    BadArgumentException,
)
from azul.service.elasticsearch_service import (
    IndexNotFoundError,
    Pagination,
)
from azul.service.repository_service import (
    EntityNotFoundError,
    RepositoryService,
)
from azul.service.source_controller import (
    SourceController,
)
from azul.types import (
    JSON,
)
from azul.uuids import (
    InvalidUUIDError,
)

log = logging.getLogger(__name__)


class RepositoryController(SourceController):

    @cached_property
    def service(self) -> RepositoryService:
        return RepositoryService()

    @cache
    def repository_plugin(self, catalog: CatalogName) -> RepositoryPlugin:
        return RepositoryPlugin.load(catalog).create(catalog)

    def search(self,
               *,
               catalog: CatalogName,
               entity_type: str,
               item_id: str | None,
               filters: str | None,
               pagination: Pagination,
               authentication: Authentication
               ) -> JSON:
        filters = self.get_filters(catalog, authentication, filters)
        try:
            response = self.service.search(catalog=catalog,
                                           entity_type=entity_type,
                                           file_url_func=self.file_url_func,
                                           item_id=item_id,
                                           filters=filters,
                                           pagination=pagination)
        except (BadArgumentException, InvalidUUIDError) as e:
            raise BadRequestError(e)
        except (EntityNotFoundError, IndexNotFoundError) as e:
            raise NotFoundError(e)
        return cast(JSON, response)

    def summary(self,
                *,
                catalog: CatalogName,
                filters: str,
                authentication: Authentication
                ) -> JSON:
        filters = self.get_filters(catalog, authentication, filters)
        try:
            response = self.service.summary(catalog, filters)
        except BadArgumentException as e:
            raise BadRequestError(e)
        return cast(JSON, response)

    def _parse_range_request_header(self,
                                    range_specifier: str
                                    ) -> Sequence[tuple[int | None, int | None]]:
        """
        >>> # noinspection PyTypeChecker
        >>> rc = RepositoryController(app=None, file_url_func=None)
        >>> rc._parse_range_request_header('bytes=100-200,300-400')
        [(100, 200), (300, 400)]

        >>> rc._parse_range_request_header('bytes=-100')
        [(None, 100)]

        >>> rc._parse_range_request_header('bytes=100-')
        [(100, None)]

        >>> rc._parse_range_request_header('foo=100')
        []

        >>> rc._parse_range_request_header('')
        Traceback (most recent call last):
        ...
        chalice.app.BadRequestError: Invalid range specifier ''

        >>> rc._parse_range_request_header('100-200')
        Traceback (most recent call last):
        ...
        chalice.app.BadRequestError: Invalid range specifier '100-200'

        >>> rc._parse_range_request_header('bytes=')
        Traceback (most recent call last):
        ...
        chalice.app.BadRequestError: Invalid range specifier 'bytes='

        >>> rc._parse_range_request_header('bytes=100')
        Traceback (most recent call last):
        ...
        chalice.app.BadRequestError: Invalid range specifier 'bytes=100'

        >>> rc._parse_range_request_header('bytes=-')
        Traceback (most recent call last):
        ...
        chalice.app.BadRequestError: Invalid range specifier 'bytes=-'

        >>> rc._parse_range_request_header('bytes=--')
        Traceback (most recent call last):
        ...
        chalice.app.BadRequestError: Invalid range specifier 'bytes=--'
        """

        def to_int_or_none(value: str) -> int | None:
            return None if value == '' else int(value)

        parsed_ranges = []
        try:
            unit, ranges = range_specifier.split('=')
            if unit == 'bytes':
                for range_spec in ranges.split(','):
                    start, end = range_spec.split('-')
                    assert start != '' or end != '', R('Empty range')
                    parsed_ranges.append((to_int_or_none(start), to_int_or_none(end)))
            else:
                assert unit != '', R('Empty range unit')
        except Exception as e:
            raise BadRequestError(f'Invalid range specifier {range_specifier!r}') from e
        return parsed_ranges

    def download_file(self,
                      catalog: CatalogName,
                      fetch: bool,
                      file_uuid: str,
                      query_params: Mapping[str, str],
                      headers: Mapping[str, str],
                      authentication: Authentication | None
                      ):
        file_version = query_params.get('version')
        replica = query_params.get('replica')
        file_name = query_params.get('fileName')
        drs_uri = query_params.get('drsUri')
        wait = query_params.get('wait')
        request_index = int(query_params.get('requestIndex', '0'))
        token = query_params.get('token')

        plugin = self.repository_plugin(catalog)
        download_cls = plugin.file_download_class()
        if TYPE_CHECKING:  # work around https://youtrack.jetbrains.com/issue/PY-44728
            download_cls = RepositoryFileDownload

        if request_index == 0:
            file = self.service.get_data_file(catalog=catalog,
                                              file_uuid=file_uuid,
                                              file_version=file_version,
                                              filters=self.get_filters(catalog, authentication, None))
            if file is None:
                raise NotFoundError(f'Unable to find file {file_uuid!r}, '
                                    f'version {file_version!r} in catalog {catalog!r}')
            file_version = file['version']
            drs_uri = file['drs_uri']
            file_size = file['size']
            if file_name is None:
                file_name = file['name']
        else:
            file_size = None
            assert file_version is not None
            assert file_name is not None

        try:
            range_specifier = headers['range']
        except KeyError:
            pass
        else:
            requested_range = self._parse_range_request_header(range_specifier)
            if requested_range == [(file_size, None)]:
                # Due to https://github.com/curl/curl/issues/10521 which causes
                # curl below 8.5.0 to fail when getting a 416 response for an
                # attempt to resume a previously completed file download,
                # instead, we return a 206 along with a `Content-Range` header,
                # which has been confirmed to work for all curl versions tested
                # (7.71.1 through 8.12.1).
                return {
                    'Status': 206,
                    'Content-Length': 0,
                    'Content-Range': f'bytes */{file_size}'
                }

        download = download_cls(file_uuid=file_uuid,
                                file_name=file_name,
                                file_version=file_version,
                                drs_uri=drs_uri,
                                replica=replica,
                                token=token)
        try:
            download.update(plugin, authentication)
        except LimitedTimeoutException as e:
            raise ServiceUnavailableError(*e.args)
        except TooManyRequestsException as e:
            raise TooManyRequestsError(*e.args)
        if download.retry_after is not None:
            retry_after = min(download.retry_after, int(1.3 ** request_index))
            query_params = {
                'version': download.file_version,
                'fileName': download.file_name,
                'requestIndex': request_index + 1
            }
            if download.drs_uri is not None:
                query_params['drsUri'] = download.drs_uri
            if download.token is not None:
                query_params['token'] = download.token
            if download.replica is not None:
                query_params['replica'] = download.replica
            if wait is not None:
                if wait == '0':
                    pass
                elif wait == '1':
                    # Sleep in the lambda but ensure that we wake up before it
                    # runs out of execution time (and before API Gateway times
                    # out) so we get a chance to return a response to the client
                    remaining_time = self.lambda_context.get_remaining_time_in_millis() / 1000
                    server_side_sleep = min(float(retry_after),
                                            remaining_time - config.api_gateway_timeout_padding - 3)
                    time.sleep(server_side_sleep)
                    retry_after = round(retry_after - server_side_sleep)
                else:
                    assert False, wait
                query_params['wait'] = wait
            return {
                'Status': 301,
                **({'Retry-After': retry_after} if retry_after else {}),
                'Location': str(self.file_url_func(catalog=catalog,
                                                   file_uuid=file_uuid,
                                                   fetch=fetch,
                                                   **query_params))
            }
        elif download.location is not None:
            log_data = {
                'file_name': file_name,
                'file_uuid': file_uuid,
                'file_version': file_version,
                'file_size': file_size,
                'catalog': catalog,
                'fetch': fetch,
                **{
                    k: headers.get(k)
                    for k in ('range', 'host', 'user-agent', 'x-forwarded-for')
                }
            }
            log.info('Download of file %s', json.dumps(log_data))
            return {
                'Status': 302,
                'Location': download.location
            }
        else:
            assert download.drs_uri is None, download
            raise NotFoundError(f'File {file_uuid!r} with version {file_version!r} '
                                f'was found in catalog {catalog!r}, however no download is currently available')

    @cache
    def field_types(self, catalog: CatalogName) -> Mapping[str, FieldType]:
        """
        Returns the field type for each supported sort and filter field, using
        the name of the field as provided by clients.
        """
        result = {}
        plugin = self.service.metadata_plugin(catalog)
        for field, path in plugin.field_mapping.items():
            field_type = self.service.field_type(catalog, path)
            if isinstance(field_type, FieldType):
                result[field] = field_type
        # This field is a synthetic element of the response and will never be
        # null. Including it here helps to streamline request validation.
        accessible = plugin.special_fields.accessible
        assert accessible not in result, result
        result[accessible] = pass_thru_bool
        return result
