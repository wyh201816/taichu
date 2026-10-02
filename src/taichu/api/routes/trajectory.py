"""通用助手轨迹只读入口，不调用执行或恢复。"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from taichu.api.deps import provide_general_agent_runtime_service
from taichu.application.general_agent.service import (
    GeneralAgentConversationNotFoundError,
    GeneralAgentRuntimeService,
)
from taichu.application.trajectory.models import TrajectoryRead
from taichu.application.trajectory.query import TrajectoryProjection

router = APIRouter(prefix="/api/agent-workbench/general-assistant")


@router.get("/conversations/{conversation_id}/trajectory", response_model=TrajectoryRead)
async def read_trajectory(
    conversation_id: str,
    request: Request,
    runtime: GeneralAgentRuntimeService = Depends(provide_general_agent_runtime_service),
) -> TrajectoryRead:
    try:
        await runtime.get_conversation(conversation_id)
    except GeneralAgentConversationNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return await request.app.state.trajectory_recorder.read(conversation_id)


@router.get("/conversations/{conversation_id}/trajectory/records")
async def read_records(conversation_id: str, request: Request,
                       limit: int = Query(100, ge=1, le=200), before: int | None = None,
                       after: int | None = None, around: str | None = None,
                       request_index: int | None = None,
                       runtime: GeneralAgentRuntimeService = Depends(provide_general_agent_runtime_service)):
    trace = await read_trajectory(conversation_id, request, runtime)
    try:
        return TrajectoryProjection(trace).page(limit=limit, before=before, after=after,
                                                around=around, request_index=request_index)
    except KeyError as error:
        raise HTTPException(404, "该会话中没有这条轨迹记录。") from error


@router.get("/conversations/{conversation_id}/trajectory/records/{record_id}")
async def read_record(conversation_id: str, record_id: str, request: Request,
                      runtime: GeneralAgentRuntimeService = Depends(provide_general_agent_runtime_service)):
    trace = await read_trajectory(conversation_id, request, runtime)
    try:
        return TrajectoryProjection(trace).detail(record_id)
    except KeyError as error:
        raise HTTPException(404, "该会话中没有这条轨迹记录。") from error


@router.get("/conversations/{conversation_id}/trajectory/search")
async def search_records(conversation_id: str, request: Request,
                         q: str = Query(min_length=1, max_length=300),
                         offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                         runtime: GeneralAgentRuntimeService = Depends(provide_general_agent_runtime_service)):
    trace = await read_trajectory(conversation_id, request, runtime)
    return TrajectoryProjection(trace).search(q, offset=offset, limit=limit)


@router.get("/conversations/{conversation_id}/trajectory/export")
async def export_records(conversation_id: str, request: Request,
                         runtime: GeneralAgentRuntimeService = Depends(provide_general_agent_runtime_service)):
    trace = await read_trajectory(conversation_id, request, runtime)
    return StreamingResponse((event.model_dump_json() + "\n" for event in trace.events),
                             media_type="application/x-ndjson; charset=utf-8",
                             headers={"Content-Disposition": 'attachment; filename="trajectory.jsonl"',
                                      "X-Trajectory-Complete": str(trace.complete).lower()})
