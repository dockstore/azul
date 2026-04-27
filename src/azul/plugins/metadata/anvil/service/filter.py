from azul.service.query_service import (
    FilterStage,
)


class AnvilFilterStage(FilterStage):

    @property
    def _limit_access(self) -> bool:
        return self.entity_type != 'datasets'
