"""Tool 插件协议。"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from typing import Annotated, cast

from langchain.tools import ToolRuntime
from langchain_core.tools import InjectedToolArg, InjectedToolCallId
from pydantic import BaseModel, ConfigDict, Field

from taichu.application.capabilities import CapabilityContext
from taichu.application.invocations.models import InvocationContext


RUNTIME_INPUT_FIELDS = frozenset(
    {
        "author_grant_id",
        "external_access_grant_id",
        "idempotency_key",
    }
)


class ToolSideEffect(StrEnum):
    """Tool 的副作用等级。"""

    READ_ONLY = "read_only"
    PREVIEW = "preview"
    WRITE = "write"
    HIGH_RISK_WRITE = "high_risk_write"


class ToolAuthorizationPolicy(StrEnum):
    """Tool 是否需要作者授权。"""

    NONE = "none"
    AUTHOR_GRANT = "author_grant"
    SECOND_CONFIRMATION = "second_confirmation"


class ToolIdempotencyPolicy(StrEnum):
    """Tool 的幂等要求。"""

    NONE = "none"
    REQUIRED = "required"


class ToolReconciliationStatus(StrEnum):
    """进程中断后对真实副作用的确定性核对结果。"""

    NOT_APPLIED = "not_applied"
    SUCCEEDED = "succeeded"
    UNKNOWN = "unknown"


class ToolReconciliationResult(BaseModel):
    """Tool 自己根据真实资源状态给出的对账证据。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: ToolReconciliationStatus
    output: dict[str, object] = Field(default_factory=dict)
    evidence: dict[str, object] = Field(default_factory=dict)
    reason: str = ""


class ToolManifest(BaseModel):
    """Tool 注册与调用所需的稳定元信息。"""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    required_capabilities: frozenset[str] = frozenset()
    exposures: frozenset[str] = frozenset()
    side_effect: ToolSideEffect = ToolSideEffect.READ_ONLY
    allowed_callers: frozenset[str] = frozenset({"orchestrator"})
    requires_external_access: bool = False
    authorization_policy: ToolAuthorizationPolicy = ToolAuthorizationPolicy.NONE
    idempotency_policy: ToolIdempotencyPolicy = ToolIdempotencyPolicy.NONE
    default_timeout_seconds: float = Field(default=30, gt=0, le=600)
    retryable: bool = False


ToolHandler = Callable[
    [BaseModel, InvocationContext, CapabilityContext],
    Awaitable[BaseModel],
]

ToolReconciler = Callable[
    [BaseModel, InvocationContext, CapabilityContext],
    Awaitable[ToolReconciliationResult],
]


@dataclass(frozen=True)
class ToolPlugin:
    """插件发现机制返回的 Tool 候选。"""

    manifest: ToolManifest
    run: ToolHandler
    reconcile: ToolReconciler | None = None


@cache
def langchain_args_schema(input_schema: type[BaseModel]) -> type[BaseModel]:
    """为 LangChain 标记运行时字段，同时保留太初原始执行 Schema。"""

    annotations: dict[str, object] = {}
    for name, field in input_schema.model_fields.items():
        if name not in RUNTIME_INPUT_FIELDS:
            continue
        annotations[name] = Annotated[
            field.rebuild_annotation(),
            InjectedToolArg,
        ]
    if not annotations:
        return input_schema
    schema_type = type(
        f"{input_schema.__name__}LangChainArgs",
        (input_schema,),
        {
            "__annotations__": annotations,
            "__module__": input_schema.__module__,
        },
    )
    return cast(type[BaseModel], schema_type)


@cache
def langchain_direct_args_schema(input_schema: type[BaseModel]) -> type[BaseModel]:
    """为非 ToolNode 调用显式声明官方 ``InjectedToolCallId``。"""

    base_schema = langchain_args_schema(input_schema)
    schema_type = type(
        f"{input_schema.__name__}DirectArgs",
        (base_schema,),
        {
            "__annotations__": {
                "tool_call_id": Annotated[str, InjectedToolCallId],
            },
            "__module__": input_schema.__module__,
        },
    )
    return cast(type[BaseModel], schema_type)


@cache
def langchain_agent_args_schema(input_schema: type[BaseModel]) -> type[BaseModel]:
    """在完整执行 Schema 中加入 ToolNode 负责注入的 ToolRuntime。"""

    base_schema = langchain_args_schema(input_schema)
    schema_type = type(
        f"{input_schema.__name__}AgentArgs",
        (base_schema,),
        {
            "__annotations__": {
                "runtime": ToolRuntime[None, dict[str, object]],
            },
            "__module__": input_schema.__module__,
            "model_config": ConfigDict(
                **{
                    **input_schema.model_config,
                    "arbitrary_types_allowed": True,
                }
            ),
        },
    )
    return cast(type[BaseModel], schema_type)
