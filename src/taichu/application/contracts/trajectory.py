"""轨迹追加和只读查询的公共存储边界。"""

from typing import Protocol

from taichu.application.trajectory.models import TrajectoryEvent, TrajectoryRead


class TrajectoryRepository(Protocol):
    async def append(self, events: list[TrajectoryEvent]) -> None: ...

    async def read(self, conversation_id: str) -> TrajectoryRead: ...
