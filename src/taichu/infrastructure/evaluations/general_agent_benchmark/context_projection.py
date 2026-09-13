"""脚本评测只验证确定性投影；摘要质量由真实模型验收覆盖。"""

from pathlib import Path
from hashlib import sha256
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langgraph.store.memory import InMemoryStore
from taichu.application.general_agent.context import ContextAssembler, _STABLE_MEMORY
from taichu.application.general_agent.pipeline import (
    ContextPipeline,
    ContextCapacityError,
    content_hash,
)
from taichu.infrastructure.general_agent_runs.result_files import JsonContextResultStore
from taichu.infrastructure.llm.context_tokens import ModelContextTokens


class DeterministicProjectionAssembler(ContextAssembler):
    """复用落盘、截断、来源有效性和旧记忆迁移，不伪造 LLM 摘要。"""

    def __init__(
        self, *, root: Path, memory_service, context_window_tokens: int | None = None
    ):
        super().__init__(memory_service=memory_service)
        self._window = context_window_tokens
        self._pipeline = ContextPipeline(
            model=FakeListChatModel(responses=[]),
            counter=ModelContextTokens(),
            result_store=JsonContextResultStore(root),
            store=InMemoryStore(),
            memory_service=memory_service,
        )

    async def assemble(self, run, *, phase, replan_guidance=""):
        if self._window is not None:
            counter = ModelContextTokens(windows={"synthetic-model": self._window})
            count = counter.count_request(
                [
                    SystemMessage(content="\n".join(_STABLE_MEMORY)),
                    HumanMessage(content=run.user_goal),
                ],
                [],
                "synthetic-model",
            )
            if count > self._window:
                raise ContextCapacityError(
                    "必要输入已超过评测模型窗口，无法发送摘要请求。",
                    input_tokens=count,
                    context_window_tokens=self._window,
                    current_request_sha256=sha256(run.user_goal.encode()).hexdigest(),
                    stable_memory_sha256=content_hash(list(_STABLE_MEMORY)),
                )
        run = await self._pipeline.prepare(run, extract=False)
        return await super().assemble(run, phase=phase, replan_guidance=replan_guidance)
