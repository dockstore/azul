import base64
import pickle
from typing import (
    Any,
    TYPE_CHECKING,
)

import resumablesha256

from azul import (
    R,
)

if TYPE_CHECKING:
    class Hasher:

        def hexdigest(self) -> str: ...

        def update(self, data: bytes, /) -> None: ...
else:
    Hasher = Any


def get_resumable_hasher(digest_type: str) -> Hasher:
    assert digest_type == 'sha256', R('Only sha256 is currently supported')
    return resumablesha256.sha256()


def hasher_to_str(hasher: Hasher) -> str:
    return base64.b64encode(pickle.dumps(hasher)).decode('ascii')


def hasher_from_str(s: str) -> Hasher:
    return pickle.loads(base64.b64decode(s))
