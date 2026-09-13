"""能力轻索引、候选检索与按需 Schema 加载测试。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import json
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ChatMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, PrivateAttr

from taichu.application.capabilities import CapabilityContext
from taichu.application.general_agent.capability_resolution import (
    CapabilityRetriever,
    RuntimeCapabilityRegistry,
    ToolSchemaLoader,
)
from taichu.application.general_agent.models import GeneralAgentExecutionPlan
from taichu.application.general_agent.models import (
    GeneralAgentPlanDraft,
    GeneralAgentContextEnvelope,
    GeneralAgentContextMemory,
    GeneralAgentCurrentRequest,
    GeneralAgentHistoryMemory,
    GeneralAgentMessage,
    GeneralAgentRun,
    GeneralAgentWorkingMemory,
)
from taichu.application.general_agent.orchestrator import (
    OrchestratorAgent,
    _apply_recent_chapter_scope,
)
from taichu.application.invocations.models import InvocationContext
from taichu.application.services.model_role_router import ModelRoleRouter
from taichu.application.subagents.contract import SubagentManifest, SubagentPlugin
from taichu.application.subagents.registry import SubagentRegistry
from taichu.application.tools.contract import ToolManifest, ToolPlugin
from taichu.application.tools.registry import ToolRegistry


class _SearchInput(BaseModel):
    query: str
    max_results: int = 10


class _SearchOutput(BaseModel):
    answer: str
    source_refs: list[str] = []


class _ReviewInput(BaseModel):
    text: str


class _ReviewOutput(BaseModel):
    issues: list[str]


class _RangeInput(BaseModel):
    start_order: int
    end_order: int


class _RangeOutput(BaseModel):
    content: str


async def _tool_run(
    _input: BaseModel,
    _invocation: InvocationContext,
    _context: CapabilityContext,
) -> BaseModel:
    return _SearchOutput(answer="", source_refs=[])


async def _subagent_run(
    _manifest: SubagentManifest,
    _input: BaseModel,
    _invocation: InvocationContext,
    _context: CapabilityContext,
) -> BaseModel:
    del _manifest
    return _ReviewOutput(issues=[])


@dataclass(frozen=True, slots=True)
class _NativeModelRequest:
    messages: tuple[BaseMessage, ...]
    tools: tuple[dict[str, Any], ...]
    tool_choice: str | None
    task_name: str
    model_id: str


class _MaterializeChatModel(BaseChatModel):
    model_id: str = "planning-model"
    task_name: str = "测试模型调用"
    _requests: list[_NativeModelRequest] = PrivateAttr(default_factory=list)

    @property
    def requests(self) -> tuple[_NativeModelRequest, ...]:
        return tuple(self._requests)

    @property
    def _llm_type(self) -> str:
        return "taichu-test-materialize"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable:
        formatted = [convert_to_openai_tool(tool, strict=True) for tool in tools]
        return self.bind(tools=formatted, tool_choice=tool_choice, **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: object | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del messages, stop, run_manager, kwargs
        raise AssertionError("本测试只允许异步模型调用。")

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager
        tools = tuple(kwargs.get("tools", ()))
        tool_choice = kwargs.get("tool_choice")
        request = _NativeModelRequest(
            messages=tuple(messages),
            tools=tools,
            tool_choice=tool_choice if isinstance(tool_choice, str) else None,
            task_name=str(kwargs.get("task_name", self.task_name)),
            model_id=str(kwargs.get("model_id", self.model_id)),
        )
        self._requests.append(request)
        if request.task_name == "general_writing_orchestrator.verify":
            payload = {
                "outcome": "satisfied",
                "final_answer": "已完成。",
                "issues": [],
                "should_replan": False,
            }
        elif request.task_name == "general_writing_orchestrator.plan":
            payload = {
                "rationale": "需要检索。",
                "nodes": [
                    {
                        "node_id": "retrieve",
                        "kind": "tool",
                        "capability_name": "retrieve_story_context",
                        "objective": "检索事实。",
                        "input_data": {"unknown": "秦浩轩"},
                    }
                ],
            }
        else:
            payload = {
                "rationale": "按完整 Schema 修复参数。",
                "nodes": [
                    {
                        "node_id": "retrieve",
                        "kind": "tool",
                        "capability_name": "retrieve_story_context",
                        "objective": "检索事实。",
                        "input_data": {"query": "秦浩轩"},
                    }
                ],
            }
        if request.tool_choice is None:
            raise AssertionError("结构化输出调用必须提供命名 tool_choice。")
        message = AIMessage(
            content="",
            tool_calls=[
                {
                    "id": f"call_{len(self._requests)}",
                    "name": request.tool_choice,
                    "args": payload,
                    "type": "tool_call",
                }
            ],
            usage_metadata={
                "input_tokens": 10,
                "output_tokens": 10,
                "total_tokens": 20,
            },
            response_metadata={"model_id": request.model_id},
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


def _registry() -> RuntimeCapabilityRegistry:
    tools = ToolRegistry(CapabilityContext(capabilities={}))
    tools.register(
        ToolPlugin(
            manifest=ToolManifest(
                name="retrieve_story_context",
                description="检索小说正文、知识卡和关系证据。",
                input_schema=_SearchInput,
                output_schema=_SearchOutput,
            ),
            run=_tool_run,
        )
    )
    subagents = SubagentRegistry(
        CapabilityContext(capabilities={"tool_registry": tools})
    )
    subagents.register(
        SubagentPlugin(
            manifest=SubagentManifest(
                name="consistency_reviewer",
                label="一致性审查",
                description="检查正文中的事实和因果冲突。",
                input_schema=_ReviewInput,
                output_schema=_ReviewOutput,
                artifact_types=frozenset({"review_report"}),
                model_role="consistency_reviewer",
                required_capabilities=frozenset(),
            ),
            run=_subagent_run,
        )
    )
    return RuntimeCapabilityRegistry(tools, subagents)


def test_retriever_keeps_full_light_index_without_full_schema_injection() -> None:
    registry = _registry()
    view = CapabilityRetriever(registry, limit=4).retrieve("检查正文冲突")

    assert view["能力总数"] == 2
    assert {item["name"] for item in view["完整轻量索引"]} == {
        "retrieve_story_context",
        "consistency_reviewer",
    }
    assert "input_schema" not in json.dumps(view["完整轻量索引"], ensure_ascii=False)
    candidates = {item["name"]: item for item in view["相关候选摘要"]}
    assert candidates["consistency_reviewer"]["description"]
    assert "input_outline" not in json.dumps(candidates, ensure_ascii=False)
    assert "output_outline" not in json.dumps(candidates, ensure_ascii=False)


def test_result_readback_tool_is_always_a_planning_candidate() -> None:
    tools = ToolRegistry(CapabilityContext(capabilities={}))
    tools.register(
        ToolPlugin(
            manifest=ToolManifest(
                name="read_runtime_result",
                description="维护跨步骤关键工作状态。",
                input_schema=_SearchInput,
                output_schema=_SearchOutput,
            ),
            run=_tool_run,
        )
    )
    subagents = SubagentRegistry(
        CapabilityContext(capabilities={"tool_registry": tools})
    )

    view = CapabilityRetriever(
        RuntimeCapabilityRegistry(tools, subagents),
        limit=4,
    ).retrieve("给第三章拟一个标题")

    assert "read_runtime_result" in {item["name"] for item in view["相关候选摘要"]}


def test_schema_loader_only_loads_selected_contract_and_reports_bad_input() -> None:
    loader = ToolSchemaLoader(_registry())
    valid = GeneralAgentExecutionPlan.model_validate(
        {
            "rationale": "需要检索证据。",
            "nodes": [
                {
                    "node_id": "retrieve",
                    "kind": "tool",
                    "capability_name": "retrieve_story_context",
                    "objective": "检索相关正文。",
                    "input_data": {"query": "秦浩轩境界"},
                }
            ],
        }
    )
    assert loader.validation_errors(valid) == []
    definitions = loader.selected_native_definitions(valid)
    assert [item["function"]["name"] for item in definitions] == [
        "retrieve_story_context"
    ]
    parameters = definitions[0]["function"]["parameters"]
    assert "query" in parameters["properties"]
    assert "author_grant_id" not in parameters["properties"]

    invalid = valid.model_copy(
        update={
            "nodes": [
                valid.nodes[0].model_copy(update={"input_data": {"unknown": "value"}})
            ]
        }
    )
    errors = loader.validation_errors(invalid)
    assert any("未知字段" in item for item in errors)
    assert any("缺少必填字段" in item for item in errors)
    assert any("允许字段：max_results, query" in item for item in errors)


def test_native_planning_contract_preserves_optional_input_and_output_paths() -> None:
    loader = ToolSchemaLoader(_registry())
    definition = loader.native_definitions(["retrieve_story_context"])[0]["function"]
    assert definition["parameters"]["required"] == ["query"]
    assert "answer" in definition["description"]
    assert "source_refs.0" in definition["description"]


def test_plan_tool_specializes_node_inputs_without_requiring_bound_values() -> None:
    loader = ToolSchemaLoader(_registry())
    tool = loader.plan_output_tool(
        GeneralAgentPlanDraft,
        ["retrieve_story_context", "consistency_reviewer"],
        max_plan_nodes=24,
    )
    function = tool["function"]
    assert function["strict"] is False
    nodes = function["parameters"]["properties"]["nodes"]
    assert nodes["maxItems"] == 24
    variants = {
        item["properties"]["capability_name"]["enum"][0]: item
        for item in nodes["items"]["anyOf"]
    }
    retrieve = variants["retrieve_story_context"]["properties"]
    assert retrieve["kind"]["enum"] == ["tool"]
    assert retrieve["input_data"]["properties"]["query"]["type"] == "string"
    assert retrieve["input_data"]["additionalProperties"] is False
    assert not retrieve["input_data"].get("required")
    review = variants["consistency_reviewer"]
    assert review["properties"]["kind"]["enum"] == ["subagent"]
    assert "issues.0" in review["description"]
    # text 可以由绑定提供，但不能伪装成对象或数组。
    assert review["properties"]["input_data"]["properties"]["text"]["type"] == "string"
    assert not review["properties"]["input_data"].get("required")


def test_real_read_draft_review_contract_rejects_screenshot_errors() -> None:
    from taichu.application.subagents.models import DraftingInput, DraftingOutput
    from taichu.application.tools.models import (
        ReadManuscriptInput,
        ReadManuscriptOutput,
    )

    registry = _registry()
    registry._tools.register(
        ToolPlugin(
            manifest=ToolManifest(
                name="read_manuscript",
                description="读取正文。",
                input_schema=ReadManuscriptInput,
                output_schema=ReadManuscriptOutput,
            ),
            run=_tool_run,
        )
    )
    registry._subagents.register(
        SubagentPlugin(
            manifest=SubagentManifest(
                name="drafting",
                label="正文初稿生成",
                description="生成正文候选。",
                input_schema=DraftingInput,
                output_schema=DraftingOutput,
                artifact_types=frozenset({"manuscript_candidate"}),
                model_role="drafting",
                required_capabilities=frozenset(),
            ),
            run=_subagent_run,
        )
    )
    loader = ToolSchemaLoader(registry)
    raw = {
        "rationale": "读取后写作并审查。",
        "nodes": [
            {
                "node_id": "read",
                "kind": "tool",
                "capability_name": "read_manuscript",
                "objective": "读取参考章节。",
                "input_data": {"chapter_ids": "99,100"},
            },
            {
                "node_id": "draft",
                "kind": "subagent",
                "capability_name": "drafting",
                "objective": "写开头。",
                "dependencies": ["read"],
                "input_data": {"writing_goal": "写开头", "target_chars": 400},
            },
            {
                "node_id": "review",
                "kind": "subagent",
                "capability_name": "consistency_reviewer",
                "objective": "审查草稿。",
                "dependencies": ["draft"],
                "input_bindings": [
                    {
                        "source_node_id": "draft",
                        "source_path": "manuscript_candidate",
                        "target_path": "text",
                    }
                ],
            },
        ],
    }
    errors = loader.validation_errors(GeneralAgentExecutionPlan.model_validate(raw))
    assert any("chapter_ids" in error for error in errors)
    assert any("manuscript_candidate" in error for error in errors)
    raw["nodes"][0]["input_data"] = {"start_order": 99, "end_order": 100}
    raw["nodes"][2]["input_bindings"][0]["source_path"] = "text"
    assert loader.validation_errors(GeneralAgentExecutionPlan.model_validate(raw)) == []

    tool = loader.plan_output_tool(
        GeneralAgentPlanDraft,
        ["read_manuscript", "drafting", "consistency_reviewer"],
        max_plan_nodes=24,
    )
    variants = tool["function"]["parameters"]["properties"]["nodes"]["items"]["anyOf"]
    read_input = variants[0]["properties"]["input_data"]
    assert read_input["properties"]["chapter_ids"]["type"] == "array"
    assert "text" in variants[1]["description"]


def test_plan_can_select_registered_capabilities_outside_loaded_candidates() -> None:
    loader = ToolSchemaLoader(_registry())
    tool = loader.plan_output_tool(
        GeneralAgentPlanDraft,
        ["retrieve_story_context"],
        max_plan_nodes=24,
        include_unloaded=True,
    )
    variants = tool["function"]["parameters"]["properties"]["nodes"]["items"]["anyOf"]
    review = next(
        item
        for item in variants
        if "consistency_reviewer" in item["properties"]["capability_name"]["enum"]
    )
    assert review["properties"]["kind"]["enum"] == ["subagent"]
    assert review["properties"]["input_data"]["additionalProperties"] is True
    assert "入选后" in review["description"]
    selected = loader.plan_output_tool(
        GeneralAgentPlanDraft,
        ["retrieve_story_context"],
        max_plan_nodes=24,
    )
    selected_variants = selected["function"]["parameters"]["properties"]["nodes"][
        "items"
    ]["anyOf"]
    assert len(selected_variants) == 1


def test_binding_validation_checks_nested_paths_and_value_types() -> None:
    from taichu.application.general_agent.capability_resolution import (
        CapabilityContract,
        _validate_binding_contract,
    )
    from taichu.application.general_agent.models import (
        GeneralAgentNodeKind,
        GeneralAgentPlanNode,
    )
    from taichu.application.subagents.models import DraftingInput
    from taichu.application.tools.models import (
        ReadManuscriptInput,
        ReadManuscriptOutput,
    )

    source = CapabilityContract(
        "read_manuscript",
        GeneralAgentNodeKind.TOOL,
        "读取正文",
        ReadManuscriptInput,
        ReadManuscriptOutput,
    )
    target = CapabilityContract(
        "drafting", GeneralAgentNodeKind.SUBAGENT, "草稿", DraftingInput
    )
    node = GeneralAgentPlanNode(
        node_id="draft", kind="subagent", capability_name="drafting", objective="创作"
    )

    def check(path, destination):
        return _validate_binding_contract(
            node, path, destination, "read", source, target
        )

    assert any(
        "类型不兼容" in error
        for error in check("chunks", "source_request.direct_context")
    )
    assert any(
        "不存在" in error
        for error in check("chunks.0.unknown", "source_request.direct_context")
    )
    assert any(
        "不存在" in error
        for error in check("chunks.0.content", "source_request.unknown")
    )
    assert check("chunks.0.content", "source_request.direct_context") == []
    assert check("output.chunks.0.content", "source_request.direct_context") == []


def test_verification_preserves_rejected_candidate_and_review_as_audit_evidence() -> (
    None
):
    from unittest.mock import AsyncMock
    from taichu.application.general_agent.models import (
        GeneralAgentNodeRun,
        GeneralAgentVerification,
    )

    registry = _registry()
    orchestrator = OrchestratorAgent(
        llm=_MaterializeChatModel(),
        model_router=ModelRoleRouter("planning-model"),
        tool_registry=registry._tools,
        subagent_registry=registry._subagents,
    )
    run = GeneralAgentRun(
        run_id="general_run_20260904_000000_abcdef",
        task_id="task",
        conversation_id="conversation",
        request_index=1,
        user_goal="写开头并检查。",
        plan_revision=1,
        created_at="2026-09-04T00:00:00Z",
        updated_at="2026-09-04T00:00:00Z",
        started_at="2026-09-04T00:00:00Z",
        node_runs=[
            GeneralAgentNodeRun(
                node_id="draft",
                plan_revision=1,
                kind="subagent",
                capability_name="drafting",
                objective="开头",
                status="success",
                output={
                    "artifact_type": "manuscript_candidate",
                    "text": "秦浩轩推开门。",
                },
            ),
            GeneralAgentNodeRun(
                node_id="review",
                plan_revision=1,
                kind="subagent",
                capability_name="consistency_reviewer",
                objective="检查",
                status="success",
                dependencies=["draft"],
                output={
                    "artifact_type": "consistency_review",
                    "verdict": "存在需要核对的冲突。",
                    "issues": [{"severity": "major", "problem": "人物位置矛盾。"}],
                },
            ),
        ],
    )
    # 大量来源标识仍保存在原始产物中，不应在每份验收记录中重复投影。
    run.node_runs[0].output["source_refs"] = [
        f"manuscript:chapter-{index}:0-3000" for index in range(2000)
    ]
    # 即使事实投影没有这些节点，验收仍须看到本次生成和审查的真实记录。
    complete = AsyncMock(
        return_value=GeneralAgentVerification(
            outcome="satisfied",
            final_answer="人物动机与后续规划已整理。",
        )
    )
    orchestrator._complete_json = complete
    decision = asyncio.run(orchestrator.verify(run, context=None))
    assert "秦浩轩推开门。" in decision.final_answer
    assert complete.await_count == 1
    audit = complete.call_args_list[0].kwargs["working_payload"]["本次验收记录"]
    assert audit[0]["output"]["text"] == "秦浩轩推开门。"
    assert audit[1]["output"]["issues"][0]["severity"] == "major"
    assert "source_refs" not in audit[0]["output"]
    assert len(run.node_runs[0].output["source_refs"]) == 2000
    assert "人物动机与后续规划已整理。" in decision.final_answer
    assert "存在需要核对的冲突。" in decision.final_answer
    assert "人物位置矛盾。" in decision.final_answer
    assert "严重" in decision.final_answer

    # 请求真实修改时，仍返回重规划决定，不提前交付待修候选。
    complete.return_value = GeneralAgentVerification(
        outcome="partial",
        final_answer="需要修改后重新检查。",
        should_replan=True,
    )
    replanning = asyncio.run(orchestrator.verify(run, context=None))
    assert replanning.should_replan
    assert replanning.final_answer == "需要修改后重新检查。"


def test_schema_loader_canonicalizes_unambiguous_range_aliases() -> None:
    tools = ToolRegistry(CapabilityContext(capabilities={}))
    tools.register(
        ToolPlugin(
            manifest=ToolManifest(
                name="read_range",
                description="按顺序范围读取。",
                input_schema=_RangeInput,
                output_schema=_RangeOutput,
            ),
            run=_tool_run,
        )
    )
    loader = ToolSchemaLoader(
        RuntimeCapabilityRegistry(
            tools,
            SubagentRegistry(CapabilityContext(capabilities={})),
        )
    )
    plan = GeneralAgentExecutionPlan.model_validate(
        {
            "rationale": "读取范围。",
            "nodes": [
                {
                    "node_id": "read",
                    "kind": "tool",
                    "capability_name": "read_range",
                    "objective": "读取第八至第十章。",
                    "input_data": {"order_range": {"start": 8, "end": 10}},
                }
            ],
        }
    )

    normalized = loader.canonicalize_input_aliases(plan)

    assert normalized.nodes[0].input_data == {
        "start_order": 8,
        "end_order": 10,
    }
    assert loader.validation_errors(normalized) == []

    shorthand = plan.model_copy(
        update={
            "nodes": [
                plan.nodes[0].model_copy(
                    update={"input_data": {"start": 81, "end": 100}}
                )
            ]
        }
    )
    normalized_shorthand = loader.canonicalize_input_aliases(shorthand)
    assert normalized_shorthand.nodes[0].input_data == {
        "start_order": 81,
        "end_order": 100,
    }


def test_schema_loader_validates_binding_source_and_target_contracts() -> None:
    loader = ToolSchemaLoader(_registry())
    plan = GeneralAgentExecutionPlan.model_validate(
        {
            "rationale": "检索后审查。",
            "nodes": [
                {
                    "node_id": "retrieve",
                    "kind": "tool",
                    "capability_name": "retrieve_story_context",
                    "objective": "检索事实。",
                    "input_data": {"query": "秦浩轩"},
                },
                {
                    "node_id": "review",
                    "kind": "subagent",
                    "capability_name": "consistency_reviewer",
                    "objective": "审查事实。",
                    "dependencies": ["retrieve"],
                    "input_bindings": [
                        {
                            "source_node_id": "retrieve",
                            "source_path": "missing_content",
                            "target_path": "missing_text",
                        }
                    ],
                },
            ],
        }
    )

    errors = loader.validation_errors(plan)

    assert any("目标字段 missing_text 不存在" in item for item in errors)
    assert any("来源路径 missing_content 不存在" in item for item in errors)


def test_recent_chapter_scope_replaces_model_guessed_structure_binding() -> None:
    plan = GeneralAgentExecutionPlan.model_validate(
        {
            "rationale": "读取最近章节。",
            "nodes": [
                {
                    "node_id": "structure",
                    "kind": "tool",
                    "capability_name": "get_novel_structure",
                    "objective": "读取结构。",
                },
                {
                    "node_id": "read_recent",
                    "kind": "tool",
                    "capability_name": "read_manuscript",
                    "objective": "读取最近20章正文。",
                    "dependencies": ["structure"],
                    "input_bindings": [
                        {
                            "source_node_id": "structure",
                            "source_path": "volumes",
                            "target_path": "chapter_ids",
                        }
                    ],
                },
            ],
        }
    )

    normalized = _apply_recent_chapter_scope(plan, 20)

    assert normalized.nodes[1].input_data == {"recent_count": 20}
    assert normalized.nodes[1].input_bindings == []


def test_orchestrator_sends_schema_only_through_native_tools() -> None:
    registry = _registry()
    model = _MaterializeChatModel()
    orchestrator = OrchestratorAgent(
        llm=model,
        model_router=ModelRoleRouter("planning-model"),
        tool_registry=registry._tools,
        subagent_registry=registry._subagents,
    )
    run = GeneralAgentRun(
        run_id="general_run_20260822_120000_abcdef",
        task_id="task",
        conversation_id="conversation",
        request_index=1,
        user_goal="秦浩轩是谁？",
        created_at="2026-08-22T04:00:00Z",
        updated_at="2026-08-22T04:00:00Z",
        started_at="2026-08-22T04:00:00Z",
    )
    context = GeneralAgentContextEnvelope(
        phase="plan",
        stable_memory=["稳定规则"],
        long_term_memory=[
            GeneralAgentContextMemory(
                memory_id="long_term_md_1234567890abcdef1234567890abcdef",
                kind="user_preference",
                content="偏好简洁回答",
                content_sha256="1" * 64,
                basis_sha256="1" * 64,
            )
        ],
        history_memory=GeneralAgentHistoryMemory(
            summary="更早对话摘要",
            messages=[
                GeneralAgentMessage(
                    role="user",
                    content="上一轮问题",
                    created_at="2026-08-22T03:00:00Z",
                ),
                GeneralAgentMessage(
                    role="assistant",
                    content="上一轮回答",
                    created_at="2026-08-22T03:01:00Z",
                ),
            ],
            total_message_count=2,
        ),
        working_memory=GeneralAgentWorkingMemory(unresolved_issues=["仍需取证"]),
        current_request=GeneralAgentCurrentRequest(content=run.user_goal),
    )

    plan = asyncio.run(orchestrator.plan(run, context=context))
    verification = asyncio.run(orchestrator.verify(run, context=context))

    assert plan.nodes[0].input_data == {"query": "秦浩轩"}
    assert verification.final_answer == "已完成。"
    assert [request.task_name for request in model.requests] == [
        "general_writing_orchestrator.plan",
        "general_writing_orchestrator.plan.materialize",
        "general_writing_orchestrator.verify",
    ]
    first_messages = model.requests[0].messages
    second_messages = model.requests[1].messages
    verify_messages = model.requests[2].messages
    assert [_message_role(message) for message in first_messages] == [
        "system",
        "developer",
        "developer",
        "user",
        "assistant",
        "developer",
        "user",
    ]
    assert "Static Capability Index" in str(first_messages[0].content)
    assert '"长期记忆"' in str(first_messages[1].content)
    assert '"历史对话摘要"' in str(first_messages[2].content)
    assert '"工作记忆"' in str(first_messages[-2].content)
    assert str(first_messages[-1].content) == run.user_goal
    all_message_text = "\n".join(
        str(message.content)
        for request in model.requests
        for message in request.messages
    )
    assert '"input_schema"' not in all_message_text
    assert '"output_schema"' not in all_message_text
    assert '"properties"' not in all_message_text
    assert '"入选能力完整契约"' not in all_message_text
    first_tool_names = [_tool_name(tool) for tool in model.requests[0].tools]
    second_tool_names = [_tool_name(tool) for tool in model.requests[1].tools]
    verify_tool_names = [_tool_name(tool) for tool in model.requests[2].tools]
    assert first_tool_names == [
        "consistency_reviewer",
        "retrieve_story_context",
        "GeneralAgentPlanDraft",
    ]
    assert second_tool_names == [
        "retrieve_story_context",
        "GeneralAgentPlanDraft",
    ]
    assert verify_tool_names == ["GeneralAgentVerification"]
    assert [request.tool_choice for request in model.requests] == [
        "GeneralAgentPlanDraft",
        "GeneralAgentPlanDraft",
        "GeneralAgentVerification",
    ]
    retrieve_schema = model.requests[0].tools[1]["function"]["parameters"]
    assert "query" in retrieve_schema["properties"]
    plan_schema = model.requests[0].tools[-1]["function"]["parameters"]
    assert plan_schema["properties"]["nodes"]["maxItems"] == 24
    system_prompts = [
        next(
            str(message.content)
            for message in messages
            if _message_role(message) == "system"
        )
        for messages in (first_messages, second_messages, verify_messages)
    ]
    assert len(set(system_prompts)) == 1
    assert "Static Capability Index" in system_prompts[0]
    assert "当前负责结果校验" not in system_prompts[0]
    assert any(
        _message_role(message) == "developer"
        and "当前负责结果校验" in str(message.content)
        for message in verify_messages
    )


def _message_role(message: BaseMessage) -> str:
    if isinstance(message, ChatMessage):
        return message.role
    return {"human": "user", "ai": "assistant"}.get(message.type, message.type)


def _tool_name(tool: dict[str, Any]) -> str:
    return str(tool["function"]["name"])
