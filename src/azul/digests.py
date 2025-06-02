import base64
import pickle
from typing import (
    Any,
    Literal,
    TYPE_CHECKING,
)

import attrs
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


@attrs.frozen(kw_only=True)
class Digest:
    """
    A hexadecimal digest of a sequence of bytes, and the type of algorithm used
    to produce said digest. The set of supported algorithms is limited to those
    we believe to present an acceptable risk of hash collisions.
    """

    type: Literal['sha256', 'sha1', 'md5']
    value: str
