"""基于 LangChain 官方 Agent Middleware 的模型调用横切能力。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import logging
from time import perf_counter
from typing import Any
from uuid import uuid4

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from taichu.application.contracts.invocation_trace import InvocationTraceRepository
from taichu.application.invocations.models import (
    InvocationContext,
    InvocationStatus,
    InvocationTraceRecord,
    now_iso,
)
from taichu.application.services.invocation_policy_service import (
    canonical_input_hash,
)

logger = logging.getLogger(__name__)


class NamedToolChoiceMiddleware(AgentMiddleware):
    """通过模型 API 的原生 ``tool_choice`` 强制选择一个命名 Tool。"""

    def __init__(self, tool_name: str) -> None:
        super().__init__()
        if not tool_name:
            raise ValueError("命名 Tool 选择不能为空。")
        self._tool_name = tool_name

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Any,
    ) -> ModelResponse:
        return await handler(request.override(tool_choice=self._tool_name))


class ModelRequestSettingsMiddleware(AgentMiddleware):
    """把单次任务的模型参数放入官方 ``ModelRequest``，不改写提示词。"""

    def __init__(self, **settings: Any) -> None:
        super().__init__()
        self._settings = dict(settings)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Any,
    ) -> ModelResponse:
        return await handler(
            request.override(
                model_settings={**request.model_settings, **self._settings}
            )
        )


class ModelInvocationTraceMiddleware(AgentMiddleware):
    """在统一技术调用契约中记录每一次真实模型调用。"""

    def __init__(
        self,
        *,
        repository: InvocationTraceRepository | None,
        invocation: InvocationContext,
        requested_model_id: str,
        model_role: str,
        capability_name: str | None = None,
    ) -> None:
        super().__init__()
        self._repository = repository
        self._invocation = invocation
        self._requested_model_id = requested_model_id
        self._model_role = model_role
        self._capability_name = capability_name or invocation.caller_name
        self._call_sequence = 0

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Any,
    ) -> ModelResponse:
        self._call_sequence += 1
        call_sequence = self._call_sequence
        started_at = now_iso()
        timer = perf_counter()
        messages = [
            *([request.system_message] if request.system_message is not None else []),
            *request.messages,
        ]
        trace_input = {
            "messages": [
                _message_contract(message)
                for message in messages
            ],
            "tools": [_tool_contract(tool) for tool in request.tools],
            "tool_choice": request.tool_choice,
        }
        try:
            response = await handler(request)
        except Exception as error:
            await self._append(
                call_sequence=call_sequence,
                started_at=started_at,
                timer=timer,
                trace_input=trace_input,
                messages=messages,
                response=None,
                error=error,
            )
            raise
        ai_message = next(
            (
                message
                for message in reversed(response.result)
                if isinstance(message, AIMessage)
            ),
            None,
        )
        await self._append(
            call_sequence=call_sequence,
            started_at=started_at,
            timer=timer,
            trace_input=trace_input,
            messages=messages,
            response=ai_message,
            error=None,
        )
        return response

    async def _append(
        self,
        *,
        call_sequence: int,
        started_at: str,
        timer: float,
        trace_input: dict[str, Any],
        messages: Sequence[BaseMessage],
        response: AIMessage | None,
        error: Exception | None,
    ) -> None:
        if self._repository is None:
            return
        usage: Mapping[str, Any] = (
            response.usage_metadata or {} if response is not None else {}
        )
        output_chars = (
            len(
                json.dumps(
                    {
                        "content": response.content,
                        "tool_calls": response.tool_calls,
                    },
                    ensure_ascii=False,
                    default=str,
                )
            )
            if response is not None
            else 0
        )
        record = InvocationTraceRecord(
            trace_id=f"trace_{uuid4().hex}",
            capability_type="llm",
            capability_name=self._capability_name,
            task_id=self._invocation.task_id,
            run_id=self._invocation.run_id,
            call_id=(f"{self._invocation.call_id}:model:{call_sequence}"),
            parent_call_id=self._invocation.parent_call_id,
            caller_type=self._invocation.caller_type,
            caller_name=self._invocation.caller_name,
            status=(
                InvocationStatus.FAILED
                if error is not None
                else InvocationStatus.COMPLETED
            ),
            input_sha256=canonical_input_hash(trace_input),
            input_char_count=sum(len(str(message.content)) for message in messages),
            output_char_count=output_chars,
            model_role=self._model_role,
            model_id=str(
                (
                    response.response_metadata.get("model_id")
                    if response is not None
                    else None
                )
                or self._requested_model_id
            ),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            call_sequence=call_sequence,
            retry_count=0,
            started_at=started_at,
            finished_at=now_iso(),
            duration_ms=max(0, round((perf_counter() - timer) * 1000)),
            error_type=type(error).__name__ if error is not None else None,
            error_message=str(error)[:500] if error is not None else None,
        )
        try:
            await self._repository.append(record)
        except Exception:  # noqa: BLE001
            logger.exception(
                "调用追踪写入失败；模型结果仍返回，但本次审计记录可能缺失。"
            )


def _message_contract(message: BaseMessage) -> dict[str, Any]:
    """保留消息配对身份，同时避免把完整 artifact 写进追踪记录。"""

    payload: dict[str, Any] = {
        "type": message.type,
        "content": message.content,
        "id": message.id,
    }
    if isinstance(message, AIMessage):
        payload["tool_calls"] = [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "args": item.get("args"),
            }
            for item in message.tool_calls
        ]
        payload["invalid_tool_calls"] = [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "args": item.get("args"),
                "error": item.get("error"),
            }
            for item in message.invalid_tool_calls
        ]
    if isinstance(message, ToolMessage):
        payload.update(
            {
                "tool_call_id": message.tool_call_id,
                "name": message.name,
                "status": message.status,
                "artifact_sha256": (
                    canonical_input_hash({"artifact": message.artifact})
                    if message.artifact is not None
                    else None
                ),
            }
        )
    return payload


def _tool_contract(tool: BaseTool | dict[str, Any]) -> dict[str, Any]:
    if isinstance(tool, BaseTool):
        schema = tool.tool_call_schema
        return {
            "name": tool.name,
            "description": tool.description,
            "parameters": (
                schema.model_json_schema()
                if isinstance(schema, type) and issubclass(schema, BaseModel)
                else schema
            ),
        }
    return tool
