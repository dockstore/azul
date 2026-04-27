from typing import (
    Protocol,
)

from azul import (
    CatalogName,
)
from azul.lib import (
    mutable_furl,
)


class BadArgumentException(Exception):

    def __init__(self, message):
        super().__init__(message)


class FileUrlFunc(Protocol):

    def __call__(self,
                 *,
                 catalog: CatalogName,
                 file_uuid: str,
                 fetch: bool = True,
                 **params: str
                 ) -> mutable_furl: ...
