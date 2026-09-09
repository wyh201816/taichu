"""让高层编排 Agent 主动维护少量关键工作状态。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from taichu.application.agent_memory.models import (
    AgentMemoryDependency,
    AgentMemoryDependencyRelation,
    AgentMemoryEntry,
    AgentMemoryKind,
    AgentMemoryValidity,
    MemoryWriteCandidate,
    memory_now_iso,
    memory_state_sha256,
)
from taichu.application.capabilities import CapabilityContext
from taichu.application.contracts.general_agent_run import (
    GeneralAgentRunRepository,
)
from taichu.application.general_agent.models import GeneralAgentRun
from taichu.application.invocations.models import InvocationContext
from taichu.application.services.agent_memory_service import (
    AgentMemoryService,
    AgentMemoryServiceError,
)
from taichu.application.tools._shared import (
    ORCHESTRATOR_WRITE_CALLERS,
    sha256_text,
)
from taichu.application.tools.contract import (
    ToolIdempotencyPolicy,
    ToolManifest,
    ToolReconciliationResult,
    ToolReconciliationStatus,
    ToolSideEffect,
)
from taichu.application.tools.models import (
    MaintainWorkingMemoryInput,
    MaintainWorkingMemoryOutput,
)


manifest = ToolManifest(
    name="maintain_working_memory",
    description=(
        "仅在当前任务产生需要跨步骤或后续请求保留的关键状态时使用："
        "记录用户已确认约束、任务主线、关键伏笔、阶段结论或未决事项；"
        "也可用当前上下文中的 memory_id 与 state_sha256 替代或失效旧状态。"
        "普通节点结果不必重复记录，小说事实只能保存来源引用并在使用前重新取证。"
    ),
    input_schema=MaintainWorkingMemoryInput,
    output_schema=MaintainWorkingMemoryOutput,
    required_capabilities=frozenset(
        {"agent_memory_service", "general_agent_run_repository"}
    ),
    exposures=frozenset({"agent_runtime"}),
    side_effect=ToolSideEffect.WRITE,
    allowed_callers=ORCHESTRATOR_WRITE_CALLERS,
    idempotency_policy=ToolIdempotencyPolicy.REQUIRED,
    default_timeout_seconds=20,
    max_result_chars=10_000,
    retryable=True,
)


async def run(
    input_data: BaseModel,
    invocation: InvocationContext,
    context: CapabilityContext,
) -> BaseModel:
    tool_input = MaintainWorkingMemoryInput.model_validate(input_data)
    memory_service, current_run = await _runtime_state(invocation, context)
    operation_ref = _operation_ref(tool_input.idempotency_key)
    dependencies = await _current_dependencies(
        memory_service,
        tool_input,
        conversation_id=current_run.conversation_id,
        request_index=current_run.request_index,
    )

    if tool_input.operation == "invalidate":
        invalidation_target = await _require_current_memory(
            memory_service,
            memory_id=_required_target(tool_input),
            expected_state_sha256=_required_target_state(tool_input),
            conversation_id=current_run.conversation_id,
            request_index=current_run.request_index,
        )
        validity = AgentMemoryValidity(tool_input.invalidation_validity)
        updated = await memory_service.invalidate(
            invalidation_target.memory_id,
            validity=validity,
            reason=tool_input.reason,
        )
        if updated is None:
            raise AgentMemoryServiceError("目标工作记忆在失效提交前已经不存在。")
        return _output(
            operation=tool_input.operation,
            entry=updated,
            message="旧工作记忆已失效，后续正常上下文不会再把它作为当前依据。",
        )

    target: AgentMemoryEntry | None = None
    if tool_input.operation == "replace":
        target = await _require_current_memory(
            memory_service,
            memory_id=_required_target(tool_input),
            expected_state_sha256=_required_target_state(tool_input),
            conversation_id=current_run.conversation_id,
            request_index=current_run.request_index,
        )
        if any(dependency.memory_id == target.memory_id for dependency in dependencies):
            raise AgentMemoryServiceError("新状态不能把被替代的旧状态作为有效依赖。")

    kind = target.kind if target is not None else _required_kind(tool_input)
    if kind is AgentMemoryKind.FACT_REFERENCE and not tool_input.source_refs:
        raise AgentMemoryServiceError("事实引用工作记忆必须携带可重新取证的来源。")
    evidence_anchors = await memory_service.resolve_evidence_anchors(
        source_refs=tool_input.source_refs,
        artifact_refs=tool_input.artifact_refs,
    )
    entry = await memory_service.write(
        MemoryWriteCandidate(
            kind=kind,
            content=tool_input.content,
            source_refs=_deduplicate([*tool_input.source_refs, operation_ref]),
            artifact_refs=tool_input.artifact_refs,
            run_ids=[current_run.run_id],
            conversation_id=current_run.conversation_id,
            created_request_index=current_run.request_index,
            expires_after_request_index=_expiry_request_index(
                tool_input.retention,
                current_run.request_index,
            ),
            retention_priority=_retention_priority(kind),
            supersedes_memory_id=(target.memory_id if target is not None else None),
            result_type="orchestrator_working_memory",
            evidence_anchors=evidence_anchors,
            dependencies=dependencies,
        )
    )
    return _output(
        operation=tool_input.operation,
        entry=entry,
        message=(
            "新的关键状态已写入，旧状态已标记为被替代。"
            if target is not None
            else "关键状态已写入工作记忆。"
        ),
    )


async def reconcile(
    input_data: BaseModel,
    invocation: InvocationContext,
    context: CapabilityContext,
) -> ToolReconciliationResult:
    """根据操作审计引用或目标状态只读核对副作用。"""

    tool_input = MaintainWorkingMemoryInput.model_validate(input_data)
    try:
        memory_service, current_run = await _runtime_state(invocation, context)
    except (AgentMemoryServiceError, LookupError) as error:
        return ToolReconciliationResult(
            status=ToolReconciliationStatus.UNKNOWN,
            reason=str(error),
        )

    if tool_input.operation == "invalidate":
        target = await memory_service.get(_required_target(tool_input))
        if target is None or target.conversation_id != current_run.conversation_id:
            return ToolReconciliationResult(
                status=ToolReconciliationStatus.UNKNOWN,
                reason="目标工作记忆不存在或不属于当前会话。",
            )
        current_hash = memory_state_sha256(target)
        expected_validity = AgentMemoryValidity(tool_input.invalidation_validity)
        if (
            target.validity is expected_validity
            and tool_input.reason.strip() in target.invalidation_reason
        ):
            output = _output(
                operation=tool_input.operation,
                entry=target,
                message="已核实旧工作记忆处于请求的失效状态。",
            )
            return ToolReconciliationResult(
                status=ToolReconciliationStatus.SUCCEEDED,
                output=output.model_dump(mode="json"),
                evidence={
                    "memory_id": target.memory_id,
                    "state_sha256": current_hash,
                    "validity": target.validity.value,
                },
                reason="目标记录已经完成失效迁移。",
            )
        if (
            target.validity is AgentMemoryValidity.ACTIVE
            and current_hash == _required_target_state(tool_input)
        ):
            return ToolReconciliationResult(
                status=ToolReconciliationStatus.NOT_APPLIED,
                evidence={"memory_id": target.memory_id},
                reason="目标记录仍处于工具执行前的有效状态。",
            )
        return ToolReconciliationResult(
            status=ToolReconciliationStatus.UNKNOWN,
            evidence={
                "memory_id": target.memory_id,
                "state_sha256": current_hash,
                "validity": target.validity.value,
            },
            reason="目标状态发生了其他变化，无法归因于本次失效操作。",
        )

    operation_ref = _operation_ref(tool_input.idempotency_key)
    entries = await memory_service.list_for_conversation(
        current_run.conversation_id,
        include_deleted=True,
    )
    matches = [entry for entry in entries if operation_ref in entry.source_refs]
    if len(matches) == 1:
        entry = matches[0]
        if (
            tool_input.operation == "replace"
            and entry.supersedes_memory_id != _required_target(tool_input)
        ):
            return ToolReconciliationResult(
                status=ToolReconciliationStatus.UNKNOWN,
                reason="审计引用命中的记录没有替代预期目标。",
            )
        output = _output(
            operation=tool_input.operation,
            entry=entry,
            message="已通过稳定审计引用核实工作记忆写入。",
        )
        return ToolReconciliationResult(
            status=ToolReconciliationStatus.SUCCEEDED,
            output=output.model_dump(mode="json"),
            evidence={
                "memory_id": entry.memory_id,
                "operation_ref": operation_ref,
                "state_sha256": memory_state_sha256(entry),
            },
            reason="稳定幂等审计引用命中唯一工作记忆。",
        )
    if not matches:
        if tool_input.operation == "replace":
            target = await memory_service.get(_required_target(tool_input))
            if target is None:
                return ToolReconciliationResult(
                    status=ToolReconciliationStatus.UNKNOWN,
                    reason="未找到新记录，且被替代目标也已经不存在。",
                )
            if target.validity is not AgentMemoryValidity.ACTIVE or memory_state_sha256(
                target
            ) != _required_target_state(tool_input):
                return ToolReconciliationResult(
                    status=ToolReconciliationStatus.UNKNOWN,
                    reason="未找到新记录，但目标状态已经变化。",
                )
        return ToolReconciliationResult(
            status=ToolReconciliationStatus.NOT_APPLIED,
            reason="未找到本次幂等操作对应的工作记忆。",
        )
    return ToolReconciliationResult(
        status=ToolReconciliationStatus.UNKNOWN,
        evidence={"matched_memory_ids": [entry.memory_id for entry in matches]},
        reason="同一幂等审计引用命中了多条工作记忆。",
    )


async def _runtime_state(
    invocation: InvocationContext,
    context: CapabilityContext,
) -> tuple[AgentMemoryService, GeneralAgentRun]:
    memory_service = context.require("agent_memory_service", AgentMemoryService)
    raw_run_repository = context.require(
        "general_agent_run_repository",
        object,
    )
    if not isinstance(raw_run_repository, GeneralAgentRunRepository):
        raise AgentMemoryServiceError("通用 Agent 运行仓储不满足读取契约。")
    run_repository = raw_run_repository
    current_run = await run_repository.get(invocation.run_id)
    if current_run is None:
        raise AgentMemoryServiceError("当前通用 Agent 运行不存在。")
    if (
        invocation.conversation_id is None
        or invocation.conversation_id != current_run.conversation_id
    ):
        raise AgentMemoryServiceError("工具调用与当前运行的会话作用域不一致。")
    return memory_service, current_run


async def _current_dependencies(
    memory_service: AgentMemoryService,
    tool_input: MaintainWorkingMemoryInput,
    *,
    conversation_id: str,
    request_index: int,
) -> list[AgentMemoryDependency]:
    dependencies: list[AgentMemoryDependency] = []
    for basis in tool_input.basis_memories:
        entry = await _require_current_memory(
            memory_service,
            memory_id=basis.memory_id,
            expected_state_sha256=basis.expected_state_sha256,
            conversation_id=conversation_id,
            request_index=request_index,
        )
        dependencies.append(
            AgentMemoryDependency(
                memory_id=entry.memory_id,
                relation=AgentMemoryDependencyRelation.BASIS,
            )
        )
    return dependencies


async def _require_current_memory(
    memory_service: AgentMemoryService,
    *,
    memory_id: str,
    expected_state_sha256: str,
    conversation_id: str,
    request_index: int,
) -> AgentMemoryEntry:
    entry = await memory_service.get(memory_id)
    if entry is None or entry.conversation_id != conversation_id:
        raise AgentMemoryServiceError("目标工作记忆不存在或不属于当前会话。")
    if not entry.is_active(as_of=memory_now_iso(), request_index=request_index):
        raise AgentMemoryServiceError("目标工作记忆已经失效、被替代或过期。")
    if memory_state_sha256(entry) != expected_state_sha256:
        raise AgentMemoryServiceError("目标工作记忆状态已变化，必须重新组装上下文。")
    return entry


def _output(
    *,
    operation: Literal["remember", "replace", "invalidate"],
    entry: AgentMemoryEntry,
    message: str,
) -> MaintainWorkingMemoryOutput:
    return MaintainWorkingMemoryOutput(
        operation=operation,
        memory_id=entry.memory_id,
        kind=entry.kind,
        validity=entry.validity,
        content_sha256=entry.content_sha256,
        state_sha256=memory_state_sha256(entry),
        supersedes_memory_id=entry.supersedes_memory_id,
        expires_after_request_index=entry.expires_after_request_index,
        message=message,
        source_refs=entry.source_refs,
    )


def _required_target(tool_input: MaintainWorkingMemoryInput) -> str:
    if tool_input.target_memory_id is None:
        raise AgentMemoryServiceError("当前操作缺少目标工作记忆。")
    return tool_input.target_memory_id


def _required_target_state(tool_input: MaintainWorkingMemoryInput) -> str:
    if tool_input.expected_target_state_sha256 is None:
        raise AgentMemoryServiceError("当前操作缺少目标工作记忆状态哈希。")
    return tool_input.expected_target_state_sha256


def _required_kind(tool_input: MaintainWorkingMemoryInput) -> AgentMemoryKind:
    if tool_input.kind is None:
        raise AgentMemoryServiceError("新增工作记忆缺少内容类型。")
    return tool_input.kind


def _operation_ref(idempotency_key: str) -> str:
    return f"working-memory-tool:{sha256_text(idempotency_key)[:24]}"


def _expiry_request_index(retention: str, request_index: int) -> int | None:
    if retention == "current_request":
        return request_index
    if retention == "next_five_requests":
        return request_index + 5
    return None


def _retention_priority(kind: AgentMemoryKind) -> int:
    return {
        AgentMemoryKind.USER_INSTRUCTION: 100,
        AgentMemoryKind.UNRESOLVED_ISSUE: 90,
        AgentMemoryKind.TASK_SUMMARY: 80,
        AgentMemoryKind.FACT_REFERENCE: 75,
        AgentMemoryKind.WORK_NOTE: 70,
        AgentMemoryKind.RESOURCE_SUMMARY: 60,
    }[kind]


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
