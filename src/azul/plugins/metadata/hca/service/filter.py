from azul.service.query_service import (
    FilterStage,
)


class HCAFilterStage(FilterStage):

    @property
    def _limit_access(self) -> bool:
        return self.entity_type != 'projects'
