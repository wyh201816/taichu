"""专业子 Agent 的真实 Tool 取证、模型调用和结构化校验。"""

from __future__ import annotations

import json
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ModelRequest,
    ModelResponse,
    ToolCallLimitMiddleware,
    ToolRetryMiddleware,
)
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import ChatMessage, HumanMessage, ToolMessage
from langgraph.store.base import BaseStore
from pydantic import BaseModel

from taichu.application.capabilities import CapabilityContext
from taichu.application.contracts.intermediate_artifact import (
    IntermediateArtifactRepository,
)
from taichu.application.contracts.invocation_trace import InvocationTraceRepository
from taichu.application.invocations.models import (
    InvocationContext,
    InvocationEnvelope,
)
from taichu.application.invocations.middleware import (
    ModelInvocationTraceMiddleware,
    AutomaticToolChoiceMiddleware,
)
from taichu.application.services.model_role_router import ModelRoleRouter
from taichu.application.services.invocation_policy_service import (
    canonical_input_hash,
)
from taichu.application.subagents.contract import SubagentManifest
from taichu.application.subagents.models import AgentSourceRequest
from taichu.application.tools.contract import ToolSideEffect
from taichu.application.tools.registry import ToolRegistry


async def run_structured_subagent(
    *,
    manifest: SubagentManifest,
    system_prompt: str,
    input_data: BaseModel,
    invocation: InvocationContext,
    context: CapabilityContext,
) -> BaseModel:
    """收集授权来源，并交给 LangChain 官方 Agent 循环生成结构化结果。"""
    llm_value = context.capabilities.get("llm")
    if not isinstance(llm_value, BaseChatModel):
        raise TypeError("专业子 Agent 缺少 LangChain BaseChatModel。")
    router = context.require("model_role_router", ModelRoleRouter)
    input_json = input_data.model_dump_json(indent=2)
    user_prompt = (
        "请按需主动调用提供的业务工具完成专业任务；完成后，"
        f"主动调用 {manifest.output_schema.__name__} 原生结果提交接口返回结果。"
        "按照接口定义填写结果，不用普通文字或消息正文中的 JSON 代替提交。\n\n"
        f"任务输入：\n{input_json}"
    )
    model_id = invocation.model_id or router.model_for(manifest.model_role)
    model = llm_value.model_copy(
        update={
            "model_id": model_id,
            "task_type": "general_writing_subagent",
            "task_name": manifest.name,
            "taichu_run_id": invocation.run_id,
            "chapter_ids": tuple(_chapter_ids(input_data)),
            "temperature": _temperature(manifest.name),
            "max_output_tokens": min(
                manifest.limits.max_output_tokens,
                invocation.budget.max_output_tokens,
            ),
            "feature": "general_writing_assistant",
        }
    )
    llm_context = invocation.child(
        caller_type="subagent",
        caller_name=manifest.name,
        phase=f"{manifest.name}:llm",
    )
    source_middleware = SubagentSourceContextMiddleware(
        manifest=manifest,
        input_data=input_data,
        invocation=invocation,
        context=context,
    )
    model_tool_source_refs: list[str] = []

    def collect_tool_result(envelope: InvocationEnvelope[BaseModel]) -> None:
        model_tool_source_refs.extend(envelope.source_refs)

    tool_registry = context.require("tool_registry", ToolRegistry)
    agent_tools = [
        tool_registry.bind_langchain_agent_tool(
            name,
            invocation.child(
                caller_type="subagent",
                caller_name=manifest.name,
                phase=f"{manifest.name}:model_tool",
            ),
            result_sink=collect_tool_result,
        )
        for name in sorted(manifest.allowed_tools)
    ]
    store_value = context.capabilities.get("graph_store")
    agent_store = store_value if isinstance(store_value, BaseStore) else None
    middleware_stack: list[Any] = [
        AutomaticToolChoiceMiddleware(),
        ModelCallLimitMiddleware(
            run_limit=_effective_model_call_limit(manifest, invocation),
            exit_behavior="error",
        ),
        ToolCallLimitMiddleware(
            run_limit=_effective_tool_call_limit(manifest, invocation),
            exit_behavior="error",
        ),
    ]
    retryable_tools = _retryable_agent_tool_names(manifest, tool_registry)
    if retryable_tools:
        middleware_stack.append(
            ToolRetryMiddleware(
                max_retries=invocation.budget.max_retries,
                tools=cast(Any, retryable_tools),
                retry_on=(TimeoutError, ConnectionError),
                on_failure="error",
                initial_delay=0.25,
                max_delay=2.0,
                jitter=True,
            )
        )
    middleware_stack.extend(
        [
            SubagentBudgetContextMiddleware(
                tool_limit=_effective_tool_call_limit(manifest, invocation),
                model_limit=_effective_model_call_limit(manifest, invocation),
            ),
            source_middleware,
            ModelInvocationTraceMiddleware(
                repository=_trace_repository(context),
                invocation=llm_context,
                requested_model_id=model_id,
                model_role=manifest.model_role,
            ),
        ]
    )
    agent = create_agent(
        model=model,
        tools=agent_tools,
        system_prompt=system_prompt,
        middleware=middleware_stack,
        response_format=ToolStrategy(
            manifest.output_schema,
            handle_errors=True,
        ),
        store=agent_store,
        name=manifest.name,
    )
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=user_prompt)]},
        config={
            "run_name": f"general_writing_subagent.{manifest.name}",
            "metadata": {
                "taichu_run_id": invocation.run_id,
                "model_role": manifest.model_role,
            },
        },
    )
    output = result.get("structured_response")
    if not isinstance(output, manifest.output_schema):
        raise SubagentOutputValidationError(
            "LangChain Agent 没有返回符合契约的 structured_response。"
        )
    if hasattr(output, "source_refs"):
        output = output.model_copy(
            update={
                "source_refs": list(
                    dict.fromkeys(
                        [
                            *source_middleware.source_refs,
                            *model_tool_source_refs,
                        ]
                    )
                )
            }
        )
    if len(output.model_dump_json()) > manifest.limits.max_output_chars:
        raise ValueError("专业子 Agent 输出超过 Manifest 字符预算。")
    return output


class SubagentBudgetContextMiddleware(AgentMiddleware):
    """投影官方计数并预留结果输出机会，不替代或放宽官方硬预算护栏。"""

    def __init__(self, *, tool_limit: int, model_limit: int) -> None:
        super().__init__()
        self._tool_limit = tool_limit
        self._model_limit = model_limit

    async def awrap_model_call(
        self, request: ModelRequest, handler: Any,
    ) -> ModelResponse:
        used_tools = request.state.get("run_tool_call_count", {}).get("__all__", 0)
        used_models = request.state.get("run_model_call_count", 0)
        # ToolStrategy 的结果输出及一次校验修复同样占用 Tool 调用计数。
        available_reads = max(0, self._tool_limit - used_tools - 2)
        finish_now = available_reads == 0 or used_models >= self._model_limit - 1
        budget_message = ChatMessage(
            role="developer",
            content=(
                "当前工作记忆中的调用预算："
                f"最多还可补充 {available_reads} 次取证调用（本轮并行调用合计也不得超过），"
                "其余预算预留给结构化结果及必要校验修复。已有来源足够时立即提交结果；"
                "不要为了补全背景反复扩大检索范围。"
                + (
                    "现在必须停止取证，依据已有证据通过结构化输出提交结果，"
                    "在警告中明确仍缺少的证据；不得编造事实。"
                    if finish_now else ""
                )
            ),
        )
        return await handler(request.override(
            tools=[] if finish_now else request.tools,
            messages=[*request.messages[:-1], budget_message, *request.messages[-1:]],
        ))


class SubagentSourceContextMiddleware(AgentMiddleware):
    """在每次模型调用前注入一次确定性、已授权的小说来源投影。"""

    def __init__(
        self,
        *,
        manifest: SubagentManifest,
        input_data: BaseModel,
        invocation: InvocationContext,
        context: CapabilityContext,
    ) -> None:
        super().__init__()
        self._manifest = manifest
        self._input_data = input_data
        self._invocation = invocation
        self._context = context
        self._source_context: str | None = None
        self.source_refs: list[str] = []

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Any,
    ) -> ModelResponse:
        if self._source_context is None:
            self._source_context, self.source_refs = await _collect_sources(
                self._manifest,
                self._input_data,
                self._invocation,
                self._context,
            )
        source_message = ChatMessage(
            role="developer",
            content=(
                "已授权来源：\n"
                + (self._source_context or "本次没有额外来源；不得虚构小说事实。")
            ),
        )
        return await handler(
            request.override(messages=[source_message, *request.messages])
        )


async def _collect_sources(
    manifest: SubagentManifest,
    input_data: BaseModel,
    invocation: InvocationContext,
    context: CapabilityContext,
) -> tuple[str, list[str]]:
    registry = context.require("tool_registry", ToolRegistry)
    if manifest.name == "external_research":
        return await _collect_external_sources(
            manifest, input_data, invocation, registry
        )
    request = getattr(input_data, "source_request", AgentSourceRequest())
    if not isinstance(request, AgentSourceRequest):
        return "", []
    calls: list[tuple[str, dict[str, object]]] = []
    goal = _primary_goal(input_data)
    review_text = (
        str(getattr(input_data, "text", ""))
        if manifest.name == "consistency_reviewer" and request.auto_collect
        else ""
    )
    if request.include_structure:
        calls.append(("get_novel_structure", {}))
    if request.chapter_ids:
        calls.append(
            (
                "read_manuscript",
                {
                    "chapter_ids": request.chapter_ids,
                    "max_content_chars": 40_000,
                },
            )
        )
    manuscript_query = request.manuscript_query or (
        goal if request.auto_collect and manifest.name != "consistency_reviewer" else ""
    )
    knowledge_query = request.knowledge_query or (goal if request.auto_collect else "")
    retrieval_query = "\n".join(
        dict.fromkeys(
            item.strip()
            for item in (manuscript_query, knowledge_query, review_text[:4_000])
            if item.strip()
        )
    )
    if retrieval_query:
        calls.append(
            (
                "retrieve_story_context",
                {"query": retrieval_query[:20_000], "max_passages": 10},
            )
        )
    for identity in request.knowledge_identities:
        calls.append(
            (
                "resolve_knowledge_identity",
                identity.model_dump(mode="json"),
            )
        )
    if request.catalog_types:
        calls.append(
            (
                "list_knowledge_catalog",
                {
                    "knowledge_types": [item.value for item in request.catalog_types],
                    "limit": 100,
                },
            )
        )
    if request.knowledge_card_ids:
        calls.append(
            (
                "read_knowledge_cards",
                {"card_ids": request.knowledge_card_ids},
            )
        )
    chunks = [request.direct_context] if request.direct_context else []
    refs: list[str] = list(request.direct_source_refs)
    if request.upstream_artifact_refs:
        artifact_repository = _artifact_repository(context)
        for artifact_id in request.upstream_artifact_refs:
            artifact = await artifact_repository.get(artifact_id)
            if artifact is None:
                raise ValueError(f"中间产物“{artifact_id}”不存在。")
            if artifact.artifact_type not in manifest.accepted_artifact_types:
                raise ValueError(
                    f"专业子 Agent“{manifest.name}”不接受中间产物类型"
                    f"“{artifact.artifact_type}”。"
                )
            chunks.append(
                f"[upstream_artifact:{artifact.artifact_type}]\n"
                f"{json.dumps(artifact.payload, ensure_ascii=False)}"
            )
            refs.append(artifact_id)
            refs.extend(artifact.source_refs)
    for index, (tool_name, payload) in enumerate(calls):
        if index >= manifest.limits.max_tool_calls:
            break
        if tool_name not in manifest.allowed_tools:
            continue
        child = _source_invocation(
            invocation=invocation,
            registry=registry,
            tool_name=tool_name,
            input_data=payload,
            ordinal=index,
            caller_name=manifest.name,
            phase=f"{manifest.name}:source_collection",
        )
        envelope = await _invoke_langchain_tool(
            registry,
            tool_name,
            payload,
            child,
        )
        chunks.append(f"[{tool_name}]\n{envelope.output.model_dump_json(indent=2)}")
        refs.extend(envelope.source_refs)
    text = "\n\n".join(chunks)
    return text[:100_000], list(dict.fromkeys(refs))


async def _collect_external_sources(
    manifest: SubagentManifest,
    input_data: BaseModel,
    invocation: InvocationContext,
    registry: ToolRegistry,
) -> tuple[str, list[str]]:
    payload = input_data.model_dump(mode="json")
    grant_id = str(payload["external_access_grant_id"])
    external_invocation = invocation.model_copy(
        update={"external_access_grant_id": grant_id}
    )
    search_payload = {
        "query": payload["research_question"],
        "source_preferences": payload.get("source_preferences", []),
        "date_range": payload.get("date_range"),
        "max_results": payload.get("max_sources", 5),
    }
    search_invocation = _source_invocation(
        invocation=external_invocation,
        registry=registry,
        tool_name="search_external_sources",
        input_data=search_payload,
        ordinal=0,
        caller_name=manifest.name,
        phase="external_research:search",
    )
    search = await _invoke_langchain_tool(
        registry,
        "search_external_sources",
        search_payload,
        search_invocation,
    )
    chunks = [f"[search_external_sources]\n{search.output.model_dump_json(indent=2)}"]
    refs = list(search.source_refs)
    items = getattr(search.output, "items", [])
    read_limit = min(3, manifest.limits.max_tool_calls - 1)
    for index, item in enumerate(items[:read_limit], start=1):
        read_payload = {"url": item.url, "max_content_chars": 15_000}
        result = await _invoke_langchain_tool(
            registry,
            "read_external_source",
            read_payload,
            _source_invocation(
                invocation=external_invocation,
                registry=registry,
                tool_name="read_external_source",
                input_data=read_payload,
                ordinal=index,
                caller_name=manifest.name,
                phase="external_research:read",
            ),
        )
        chunks.append(
            f"[read_external_source]\n{result.output.model_dump_json(indent=2)}"
        )
        refs.extend(result.source_refs)
    return "\n\n".join(chunks)[:100_000], list(dict.fromkeys(refs))


async def _invoke_langchain_tool(
    registry: ToolRegistry,
    name: str,
    input_data: dict[str, object],
    invocation: InvocationContext,
) -> InvocationEnvelope[Any]:
    tool = registry.bind_langchain_tool(name, invocation)
    result = await tool.ainvoke(
        {
            "type": "tool_call",
            "name": name,
            "args": input_data,
            "id": invocation.call_id,
        }
    )
    if not isinstance(result, ToolMessage):
        raise TypeError(f"LangChain Tool“{name}”没有返回 ToolMessage。")
    artifact = result.artifact
    if not isinstance(artifact, InvocationEnvelope):
        raise TypeError(f"LangChain Tool“{name}”没有返回太初调用证据。")
    return artifact


def _source_invocation(
    *,
    invocation: InvocationContext,
    registry: ToolRegistry,
    tool_name: str,
    input_data: dict[str, object],
    ordinal: int,
    caller_name: str,
    phase: str,
) -> InvocationContext:
    """为确定性来源预取派生可跨恢复复用的 Tool 调用身份。"""

    manifest = registry.get_manifest(tool_name)
    parsed_input = manifest.input_schema.model_validate(input_data)
    value = uuid5(
        NAMESPACE_URL,
        (
            f"taichu:{invocation.call_id}:{phase}:{ordinal}:{tool_name}:"
            f"{canonical_input_hash(parsed_input)}"
        ),
    )
    return invocation.child(
        caller_type="subagent",
        caller_name=caller_name,
        phase=phase,
    ).model_copy(update={"call_id": f"call_{value.hex}"})


def _primary_goal(input_data: BaseModel) -> str:
    payload = input_data.model_dump(mode="json")
    for key in (
        "question",
        "summary_goal",
        "design_goal",
        "character_goal",
        "architecture_goal",
        "scene_goal",
        "writing_goal",
        "revision_goal",
        "review_goal",
        "style_target",
    ):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _chapter_ids(input_data: BaseModel) -> list[str]:
    payload = input_data.model_dump(mode="json")
    result: list[str] = []
    chapter_id = payload.get("chapter_id")
    if isinstance(chapter_id, str) and chapter_id:
        result.append(chapter_id)
    source_request = payload.get("source_request")
    if isinstance(source_request, dict):
        values = source_request.get("chapter_ids", [])
        if isinstance(values, list):
            result.extend(value for value in values if isinstance(value, str))
    return list(dict.fromkeys(result))


def _temperature(agent_name: str) -> float:
    if agent_name == "drafting":
        return 0.8
    if agent_name in {"worldbuilding", "character", "story_architecture"}:
        return 0.6
    if agent_name in {"scene_planning", "revision"}:
        return 0.4
    return 0.2


class SubagentOutputValidationError(ValueError):
    """模型输出在有限修复后仍不满足专业 Agent Schema。"""


def _trace_repository(
    context: CapabilityContext,
) -> InvocationTraceRepository | None:
    value = context.capabilities.get("invocation_trace_repository")
    return value if isinstance(value, InvocationTraceRepository) else None


def _retryable_agent_tool_names(
    manifest: SubagentManifest,
    registry: ToolRegistry,
) -> list[str]:
    """只允许官方中间件重试 Manifest 明示的无副作用读取 Tool。"""

    result: list[str] = []
    for name in sorted(manifest.allowed_tools):
        tool_manifest = registry.get_manifest(name)
        if (
            tool_manifest.retryable
            and tool_manifest.side_effect is ToolSideEffect.READ_ONLY
        ):
            result.append(name)
    return result


def _effective_tool_call_limit(
    manifest: SubagentManifest,
    invocation: InvocationContext,
) -> int:
    """单个子 Agent 的官方 loop 护栏不得超过任务级声明上限。"""

    return min(manifest.limits.max_tool_calls, invocation.budget.max_tool_calls)


def _effective_model_call_limit(
    manifest: SubagentManifest,
    invocation: InvocationContext,
) -> int:
    """给真实 Tool 循环、最终结构化输出和有限修复分别保留模型轮次。"""

    return (
        _effective_tool_call_limit(manifest, invocation)
        + manifest.repair_attempts
        + 1
    )


def _artifact_repository(
    context: CapabilityContext,
) -> IntermediateArtifactRepository:
    value = context.capabilities.get("artifact_repository")
    if not isinstance(value, IntermediateArtifactRepository):
        raise TypeError("专业子 Agent 缺少中间产物仓储。")
    return value
