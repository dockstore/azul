from enum import (
    auto,
)

from azul.queues import (
    Action,
)


class MirrorAction(Action):
    mirror_source = auto()
    mirror_partition = auto()
    mirror_file = auto()
    mirror_part = auto()
    finalize_file = auto()
