"""跨任务长期记忆的按需召回契约。"""

from typing import Protocol

from taichu.application.general_agent.models import GeneralAgentContextMemory


class LongTermMemoryRetriever(Protocol):
    """从长期维护载体中投影与当前请求相关的用户偏好。"""

    async def retrieve(
        self,
        query: str,
    ) -> list[GeneralAgentContextMemory]: ...
