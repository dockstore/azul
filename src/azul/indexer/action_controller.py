import chalice

from azul import (
    R,
)
from azul.azulclient import (
    Action,
)
from azul.chalice import (
    AppController,
)
from azul.types import (
    derived_type_params,
)


class ActionController[A: Action](AppController):

    def _load_action(self, action: str) -> A:
        action_cls = derived_type_params(type(self), root=ActionController)[A]
        assert issubclass(action_cls, Action), action_cls
        try:
            action = action_cls.from_json(action)
        except AssertionError as e:
            if R.caused(e):
                raise chalice.BadRequestError(repr(e.args))
            else:
                raise
        else:
            return action
