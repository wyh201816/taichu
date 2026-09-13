"""通用写作助手 Runtime 的任务与恢复接口。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from taichu.api.deps import (
    provide_agent_memory_service,
    provide_general_agent_event_center,
    provide_general_agent_runtime_service,
    provide_invocation_trace_reader,
)
from taichu.api.schemas.general_agent import (
    AgentMemoryListResponse,
    AgentMemoryResponse,
    GeneralAgentConversationDeleteResponse,
    GeneralAgentConversationListResponse,
    GeneralAgentConversationResponse,
    GeneralAgentContextSnapshotListResponse,
    GeneralAgentDeleteResponse,
    GeneralAgentResumeRequest,
    GeneralAgentRecoveryResponse,
    GeneralAgentRunListResponse,
    GeneralAgentRunRequest,
    GeneralAgentRunResponse,
    GeneralAgentRunSummary,
    GeneralAgentLLMReplayListResponse,
    GeneralAgentTraceListResponse,
)
from taichu.application.contracts.invocation_trace import InvocationTraceReader
from taichu.application.general_agent.events import GeneralAgentEventCenter
from taichu.application.general_agent.models import (
    GeneralAgentNodeStatus,
    GeneralAgentRun,
)
from taichu.application.general_agent.service import (
    GeneralAgentConversationNotFoundError,
    GeneralAgentRunNotFoundError,
    GeneralAgentRuntimeError,
    GeneralAgentRuntimeService,
)
from taichu.application.services.agent_memory_service import AgentMemoryService

router = APIRouter(prefix="/api/agent-workbench/general-assistant")


@router.post("/conversations/{conversation_id}/compact")
async def api_compact_context(
    conversation_id: str,
    service: GeneralAgentRuntimeService = Depends(
        provide_general_agent_runtime_service
    ),
) -> dict:
    """合并为一次待执行的会话操作，不新增消息或用户轮。"""
    try:
        request = await service.request_context_compaction(conversation_id)
    except GeneralAgentConversationNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except GeneralAgentRuntimeError as error:
        raise _unprocessable(str(error)) from error
    return {"operation": request}


@router.get("/conversations/{conversation_id}/compact")
async def api_context_compaction_status(
    conversation_id: str,
    service: GeneralAgentRuntimeService = Depends(
        provide_general_agent_runtime_service
    ),
) -> dict:
    try:
        return {"operation": await service.context_compaction_status(conversation_id)}
    except GeneralAgentConversationNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/runs", response_model=GeneralAgentRunResponse)
async def api_run_general_agent(
    request: GeneralAgentRunRequest,
    service: GeneralAgentRuntimeService = Depends(
        provide_general_agent_runtime_service
    ),
) -> GeneralAgentRunResponse:
    """同步运行到完成、失败或人工中断。"""
    try:
        run = await service.run(**request.model_dump())
    except GeneralAgentRuntimeError as error:
        raise _unprocessable(str(error)) from error
    return GeneralAgentRunResponse(run=run)


@router.post("/runs/start", response_model=GeneralAgentRunResponse)
async def api_start_general_agent(
    request: GeneralAgentRunRequest,
    service: GeneralAgentRuntimeService = Depends(
        provide_general_agent_runtime_service
    ),
) -> GeneralAgentRunResponse:
    """创建后台任务并立即返回初始检查点。"""
    try:
        run = await service.start(**request.model_dump())
    except GeneralAgentRuntimeError as error:
        raise _unprocessable(str(error)) from error
    return GeneralAgentRunResponse(run=run)


@router.get("/runs", response_model=GeneralAgentRunListResponse)
async def api_list_general_agent_runs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str = "all",
    service: GeneralAgentRuntimeService = Depends(
        provide_general_agent_runtime_service
    ),
) -> GeneralAgentRunListResponse:
    try:
        runs, total = await service.list(
            page=page,
            page_size=page_size,
            status=status,
        )
    except ValueError as error:
        raise _unprocessable(str(error)) from error
    return GeneralAgentRunListResponse(
        runs=[_summary(run) for run in runs],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/conversations",
    response_model=GeneralAgentConversationListResponse,
)
async def api_list_general_agent_conversations(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
    service: GeneralAgentRuntimeService = Depends(
        provide_general_agent_runtime_service
    ),
) -> GeneralAgentConversationListResponse:
    conversations, total = await service.list_conversations(
        page=page,
        page_size=page_size,
    )
    return GeneralAgentConversationListResponse(
        conversations=conversations,
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/conversations/{conversation_id}",
    response_model=GeneralAgentConversationResponse,
)
async def api_get_general_agent_conversation(
    conversation_id: str,
    service: GeneralAgentRuntimeService = Depends(
        provide_general_agent_runtime_service
    ),
) -> GeneralAgentConversationResponse:
    try:
        runs = await service.get_conversation(conversation_id)
    except GeneralAgentConversationNotFoundError as error:
        raise _not_found(str(error)) from error
    return GeneralAgentConversationResponse(
        conversation_id=conversation_id,
        runs=runs,
    )


@router.get(
    "/conversations/{conversation_id}/memories",
    response_model=AgentMemoryListResponse,
)
async def api_list_general_agent_memories(
    conversation_id: str,
    include_deleted: bool = False,
    service: GeneralAgentRuntimeService = Depends(
        provide_general_agent_runtime_service
    ),
    memory_service: AgentMemoryService = Depends(provide_agent_memory_service),
) -> AgentMemoryListResponse:
    """查看对话运行记忆；这些内容不属于小说事实源。"""

    try:
        await service.get_conversation(conversation_id)
    except GeneralAgentConversationNotFoundError as error:
        raise _not_found(str(error)) from error
    memories = await memory_service.list_for_conversation(
        conversation_id,
        include_deleted=include_deleted,
    )
    return AgentMemoryListResponse(
        conversation_id=conversation_id,
        memories=memories,
        total=len(memories),
    )


@router.delete(
    "/conversations/{conversation_id}",
    response_model=GeneralAgentConversationDeleteResponse,
)
async def api_delete_general_agent_conversation(
    conversation_id: str,
    service: GeneralAgentRuntimeService = Depends(
        provide_general_agent_runtime_service
    ),
) -> GeneralAgentConversationDeleteResponse:
    try:
        deleted_count = await service.delete_conversation(conversation_id)
    except GeneralAgentConversationNotFoundError as error:
        raise _not_found(str(error)) from error
    except GeneralAgentRuntimeError as error:
        raise _conflict(str(error)) from error
    return GeneralAgentConversationDeleteResponse(
        conversation_id=conversation_id,
        deleted_count=deleted_count,
    )


@router.get("/runs/stream/events")
async def api_stream_general_agent_events(
    event_center: GeneralAgentEventCenter = Depends(provide_general_agent_event_center),
) -> StreamingResponse:
    async def event_lines():
        async for event in event_center.subscribe():
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(
        event_lines(),
        media_type="application/x-ndjson; charset=utf-8",
    )


@router.get("/runs/{run_id}", response_model=GeneralAgentRunResponse)
async def api_get_general_agent_run(
    run_id: str,
    service: GeneralAgentRuntimeService = Depends(
        provide_general_agent_runtime_service
    ),
) -> GeneralAgentRunResponse:
    try:
        run = await service.get(run_id)
    except GeneralAgentRunNotFoundError as error:
        raise _not_found(str(error)) from error
    return GeneralAgentRunResponse(run=run)


@router.get(
    "/runs/{run_id}/recovery",
    response_model=GeneralAgentRecoveryResponse,
)
async def api_get_general_agent_recovery(
    run_id: str,
    service: GeneralAgentRuntimeService = Depends(
        provide_general_agent_runtime_service
    ),
) -> GeneralAgentRecoveryResponse:
    """读取官方检查点可用性和写入对账状态。"""

    try:
        recovery = await service.recovery_snapshot(run_id)
    except GeneralAgentRunNotFoundError as error:
        raise _not_found(str(error)) from error
    return GeneralAgentRecoveryResponse(recovery=recovery)


@router.get(
    "/runs/{run_id}/context-snapshots",
    response_model=GeneralAgentContextSnapshotListResponse,
)
async def api_list_general_agent_context_snapshots(
    run_id: str,
    service: GeneralAgentRuntimeService = Depends(
        provide_general_agent_runtime_service
    ),
) -> GeneralAgentContextSnapshotListResponse:
    """读取规划、重规划和校验阶段实际组装的五层上下文。"""

    try:
        snapshots = await service.list_context_snapshots(run_id)
    except GeneralAgentRunNotFoundError as error:
        raise _not_found(str(error)) from error
    return GeneralAgentContextSnapshotListResponse(
        run_id=run_id,
        snapshots=snapshots,
        total=len(snapshots),
    )


@router.get(
    "/runs/{run_id}/llm-replays",
    response_model=GeneralAgentLLMReplayListResponse,
)
async def api_list_general_agent_llm_replays(
    run_id: str,
    service: GeneralAgentRuntimeService = Depends(
        provide_general_agent_runtime_service
    ),
) -> GeneralAgentLLMReplayListResponse:
    """读取脱敏后的逐次模型消息和规范化响应，用于回放与评测。"""

    try:
        calls = await service.list_llm_replays(run_id)
    except GeneralAgentRunNotFoundError as error:
        raise _not_found(str(error)) from error
    return GeneralAgentLLMReplayListResponse(
        run_id=run_id,
        calls=calls,
        total=len(calls),
    )


@router.get(
    "/runs/{run_id}/traces",
    response_model=GeneralAgentTraceListResponse,
)
async def api_list_general_agent_traces(
    run_id: str,
    limit: int = Query(default=500, ge=1, le=2_000),
    service: GeneralAgentRuntimeService = Depends(
        provide_general_agent_runtime_service
    ),
    trace_reader: InvocationTraceReader = Depends(provide_invocation_trace_reader),
) -> GeneralAgentTraceListResponse:
    """读取一条通用 Agent 运行的脱敏 Tool、子 Agent 与 LLM 调用树。"""
    try:
        await service.get(run_id)
    except GeneralAgentRunNotFoundError as error:
        raise _not_found(str(error)) from error
    traces, total = await trace_reader.list_for_run(run_id, limit=limit)
    return GeneralAgentTraceListResponse(traces=traces, total=total)


@router.post("/runs/{run_id}/resume", response_model=GeneralAgentRunResponse)
async def api_resume_general_agent_run(
    run_id: str,
    request: GeneralAgentResumeRequest,
    service: GeneralAgentRuntimeService = Depends(
        provide_general_agent_runtime_service
    ),
) -> GeneralAgentRunResponse:
    try:
        run = await service.resume(run_id, **request.model_dump())
    except GeneralAgentRunNotFoundError as error:
        raise _not_found(str(error)) from error
    except GeneralAgentRuntimeError as error:
        raise _conflict(str(error)) from error
    return GeneralAgentRunResponse(run=run)


@router.post("/runs/{run_id}/cancel", response_model=GeneralAgentRunResponse)
async def api_cancel_general_agent_run(
    run_id: str,
    service: GeneralAgentRuntimeService = Depends(
        provide_general_agent_runtime_service
    ),
) -> GeneralAgentRunResponse:
    try:
        run = await service.cancel(run_id)
    except GeneralAgentRunNotFoundError as error:
        raise _not_found(str(error)) from error
    return GeneralAgentRunResponse(run=run)


@router.delete("/runs/{run_id}", response_model=GeneralAgentDeleteResponse)
async def api_delete_general_agent_run(
    run_id: str,
    service: GeneralAgentRuntimeService = Depends(
        provide_general_agent_runtime_service
    ),
) -> GeneralAgentDeleteResponse:
    try:
        deleted = await service.delete(run_id)
    except GeneralAgentRuntimeError as error:
        raise _conflict(str(error)) from error
    if not deleted:
        raise _not_found(f"通用写作助手任务“{run_id}”不存在。")
    return GeneralAgentDeleteResponse(run_id=run_id, deleted=True)


@router.get("/memories/{memory_id}", response_model=AgentMemoryResponse)
async def api_get_general_agent_memory(
    memory_id: str,
    memory_service: AgentMemoryService = Depends(provide_agent_memory_service),
) -> AgentMemoryResponse:
    memory = await memory_service.get(memory_id)
    if memory is None:
        raise _not_found(f"运行记忆“{memory_id}”不存在。")
    return AgentMemoryResponse(memory=memory)


def _summary(run: GeneralAgentRun) -> GeneralAgentRunSummary:
    current = [
        item for item in run.node_runs if item.plan_revision == run.plan_revision
    ]
    return GeneralAgentRunSummary(
        run_id=run.run_id,
        conversation_id=run.conversation_id,
        request_index=run.request_index,
        agent_name=run.agent_name,
        user_goal=run.user_goal,
        status=run.status.value,
        scope_type=run.scope.scope_type,
        plan_revision=run.plan_revision,
        replan_count=run.replan_count,
        completed_node_count=sum(
            1 for item in current if item.status is GeneralAgentNodeStatus.SUCCESS
        ),
        failed_node_count=sum(
            1 for item in current if item.status is GeneralAgentNodeStatus.FAILED
        ),
        total_node_count=len(current),
        waiting_human_kind=(
            run.pending_human_request.kind if run.pending_human_request else None
        ),
        final_answer_preview=run.final_answer[:300],
        memory_count=len(run.memory_refs),
        context_snapshot_id=run.context_snapshot_id,
        context_compressed=run.compression_stats.compressed,
        estimated_context_tokens=run.compression_stats.estimated_token_count,
        created_at=run.created_at,
        updated_at=run.updated_at,
        finished_at=run.finished_at,
    )


def _not_found(message: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"error": {"code": "NOT_FOUND", "message": message}},
    )


def _unprocessable(message: str) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"error": {"code": "GENERAL_AGENT_INVALID_REQUEST", "message": message}},
    )


def _conflict(message: str) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"error": {"code": "GENERAL_AGENT_STATE_CONFLICT", "message": message}},
    )
