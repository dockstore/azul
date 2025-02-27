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


class ActionController(AppController):

    def _load_action(self, action: str) -> Action:
        try:
            action = Action.from_json(action)
        except AssertionError as e:
            if R.caused(e):
                raise chalice.BadRequestError(repr(e.args))
            else:
                raise
        else:
            return action
