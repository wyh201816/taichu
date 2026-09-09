"""通用 Agent 任务级 Tool 调用预算合同。"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

_SHA256_PATTERN = r"^[a-f0-9]{64}$"


class _ToolBudgetModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class GeneralAgentToolBudgetOwner(_ToolBudgetModel):
    """预算按长期会话中的单次业务运行隔离。"""

    conversation_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)


class GeneralAgentToolBudgetClaim(_ToolBudgetModel):
    """一次逻辑 Tool 调用的稳定、可恢复占位。"""

    logical_call_id: str = Field(
        pattern=r"^tool_budget_call_[a-f0-9]{64}$",
    )
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    input_sha256: str = Field(pattern=_SHA256_PATTERN)
    call_id: str = Field(min_length=1, max_length=128)
    parent_call_id: str | None = Field(default=None, max_length=128)
    caller_name: str = Field(min_length=1, max_length=128)
    phase: str = Field(min_length=1, max_length=128)
    claimed_at: str = Field(min_length=1, max_length=64)


class GeneralAgentToolBudgetSnapshot(_ToolBudgetModel):
    """持久化预算权威状态；LangGraph state 只能投影该状态。"""

    owner: GeneralAgentToolBudgetOwner
    limit: int = Field(ge=1, le=100)
    used: int = Field(ge=0, le=100)
    remaining: int = Field(ge=0, le=100)
    claims: tuple[GeneralAgentToolBudgetClaim, ...] = ()

    @model_validator(mode="after")
    def validate_accounting(self) -> GeneralAgentToolBudgetSnapshot:
        if self.used != len(self.claims):
            raise ValueError("Tool 调用预算已用数量与占位记录不一致。")
        if self.remaining != self.limit - self.used:
            raise ValueError("Tool 调用预算剩余数量不一致。")
        logical_ids = [claim.logical_call_id for claim in self.claims]
        if len(logical_ids) != len(set(logical_ids)):
            raise ValueError("Tool 调用预算包含重复逻辑调用标识。")
        return self


class GeneralAgentToolBudgetErrorCode(StrEnum):
    OWNER_NOT_INITIALIZED = "general_agent_tool_budget_owner_not_initialized"
    LIMIT_CONFLICT = "general_agent_tool_budget_limit_conflict"
    CLAIM_CONFLICT = "general_agent_tool_budget_claim_conflict"
    LIMIT_EXCEEDED = "general_agent_tool_budget_limit_exceeded"
    RECORD_CORRUPT = "general_agent_tool_budget_record_corrupt"
    UNAVAILABLE = "general_agent_tool_budget_unavailable"


class GeneralAgentToolBudgetError(RuntimeError):
    """预算合同失败；稳定错误码供 Runtime 判定。"""

    def __init__(
        self,
        code: GeneralAgentToolBudgetErrorCode,
        message: str,
    ) -> None:
        super().__init__(message)
        self.code = code


class GeneralAgentToolBudgetOwnerNotInitializedError(GeneralAgentToolBudgetError):
    def __init__(self) -> None:
        super().__init__(
            GeneralAgentToolBudgetErrorCode.OWNER_NOT_INITIALIZED,
            "通用 Agent Tool 调用预算尚未初始化。",
        )


class GeneralAgentToolBudgetLimitConflictError(GeneralAgentToolBudgetError):
    def __init__(self) -> None:
        super().__init__(
            GeneralAgentToolBudgetErrorCode.LIMIT_CONFLICT,
            "同一通用 Agent 运行的 Tool 调用预算上限不一致。",
        )


class GeneralAgentToolBudgetClaimConflictError(GeneralAgentToolBudgetError):
    def __init__(self) -> None:
        super().__init__(
            GeneralAgentToolBudgetErrorCode.CLAIM_CONFLICT,
            "同一 Tool 逻辑调用标识对应了不同工具或输入。",
        )


class GeneralAgentToolBudgetExceededError(GeneralAgentToolBudgetError):
    def __init__(self) -> None:
        super().__init__(
            GeneralAgentToolBudgetErrorCode.LIMIT_EXCEEDED,
            "本次通用 Agent 任务的 Tool 调用总预算已用尽。",
        )


class GeneralAgentToolBudgetRecordCorruptError(GeneralAgentToolBudgetError):
    def __init__(self, message: str = "Tool 调用预算记录损坏。") -> None:
        super().__init__(GeneralAgentToolBudgetErrorCode.RECORD_CORRUPT, message)


class GeneralAgentToolBudgetUnavailableError(GeneralAgentToolBudgetError):
    def __init__(self) -> None:
        super().__init__(
            GeneralAgentToolBudgetErrorCode.UNAVAILABLE,
            "Tool 调用预算仓储当前不可用。",
        )


@runtime_checkable
class GeneralAgentToolBudgetRepository(Protocol):
    """任务级共享 Tool 预算的原子持久化协议。"""

    async def initialize(
        self,
        owner: GeneralAgentToolBudgetOwner,
        limit: int,
    ) -> GeneralAgentToolBudgetSnapshot: ...

    async def claim(
        self,
        owner: GeneralAgentToolBudgetOwner,
        claim: GeneralAgentToolBudgetClaim,
    ) -> GeneralAgentToolBudgetSnapshot: ...

    async def read(
        self,
        owner: GeneralAgentToolBudgetOwner,
    ) -> GeneralAgentToolBudgetSnapshot | None: ...

    async def delete(self, owner: GeneralAgentToolBudgetOwner) -> bool: ...


__all__ = [
    "GeneralAgentToolBudgetClaim",
    "GeneralAgentToolBudgetClaimConflictError",
    "GeneralAgentToolBudgetError",
    "GeneralAgentToolBudgetErrorCode",
    "GeneralAgentToolBudgetExceededError",
    "GeneralAgentToolBudgetLimitConflictError",
    "GeneralAgentToolBudgetOwner",
    "GeneralAgentToolBudgetOwnerNotInitializedError",
    "GeneralAgentToolBudgetRecordCorruptError",
    "GeneralAgentToolBudgetRepository",
    "GeneralAgentToolBudgetSnapshot",
    "GeneralAgentToolBudgetUnavailableError",
]
