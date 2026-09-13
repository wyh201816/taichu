"""负责理解、规划、校验和收敛的高层编排 Agent。"""

from __future__ import annotations

import json
import re
from typing import Any, cast, TypeVar

from langchain.agents import create_agent
from langchain.agents.middleware import InputAgentState, ModelRequest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    ChatMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.output_parsers.openai_tools import PydanticToolsParser
from langchain_core.runnables import RunnableConfig
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, ValidationError

from taichu.application.contracts.invocation_trace import InvocationTraceRepository
from taichu.application.general_agent.models import (
    GeneralAgentContextEnvelope,
    GeneralAgentExecutionPlan,
    GeneralAgentNodeKind,
    GeneralAgentNodeStatus,
    GeneralAgentPlanDraft,
    GeneralAgentRun,
    GeneralAgentVerification,
)
from taichu.application.general_agent.capability_resolution import (
    CapabilityRetriever,
    RuntimeCapabilityRegistry,
    ToolSchemaLoader,
)
from taichu.application.general_agent.request_analysis import (
    explicit_chapter_orders,
    is_explicit_chapter_content_request,
    recent_chapter_count,
)
from taichu.application.general_agent.delivery import compose_verified_delivery
from taichu.application.invocations.models import (
    InvocationContext,
)
from taichu.application.invocations.middleware import (
    ModelInvocationTraceMiddleware,
    ModelRequestSettingsMiddleware,
    NamedToolChoiceMiddleware,
)
from taichu.application.services.model_role_router import ModelRoleRouter
from taichu.application.subagents.registry import SubagentRegistry
from taichu.application.tools.registry import ToolRegistry

from taichu.application.general_agent.pipeline import (
    ACTIVE_PIPELINE,
    ContextPipelineMiddleware,
    ContextCapacityError,
    message_id,
)

_OutputModel = TypeVar("_OutputModel", bound=BaseModel)

_PLAN_SYSTEM_PROMPT = """你是太初通用写作助手的高层编排 Agent，负责一次形成可执行的最小充分 DAG。
你的职责是理解目标、从能力检索结果选择最小充分路径、维护依赖和全局收敛；你不是设定、写作或审校专家。

硬性规则：
1. 只能使用能力目录中真实存在的 Tool 和专业子 Agent，不得创造能力名称。
2. 小问题不得强行扩展成长链路；无需项目事实或专业执行时可直接回答。
3. 涉及小说事实时必须安排取证能力，不能靠自身猜测。
4. Tool 是确定性原子能力；需要专业判断、写作、规划或审校时选择子 Agent。
5. 节点依赖必须形成无环图；可并行的节点不要制造虚假依赖。
6. 候选能力的输入 Schema 只通过模型 API 原生 tools 参数提供；必须依据这些原生工具契约填写当前已能确定的 input_data。
7. 已知明确章节序号且用户要求读取、概括或总结正文时，必须选择 read_manuscript 直接读取；原文位置未知、相关知识或跨来源证据统一使用 retrieve_story_context。
8. 参数依赖上游节点结果时必须使用 input_bindings 声明；source_path 以上游 output 为根，target_path 以当前能力输入对象为根。
9. 未经用户明确允许，不得安排外部研究能力。
10. 写 Tool 可以出现在计划中，但 Runtime 会在执行前暂停并请求作者授权。
11. 信息缺口会实质改变结果时才澄清；不要询问可以从小说正文或知识库取得的事实。
12. 所有面向作者的内容使用中文。
13. 运行记忆不是小说事实；fact_reference 只能提示你安排正文或统一召回重新取证。
14. 工作记忆由独立模型自动增量抽取。工具结果展示确定性预览，标为截断的结果可用 read_runtime_result 按完整引用、字段或范围回读。
15. 完整轻量索引及候选摘要中的能力都是真实注册能力；不得创造索引中不存在的能力。参数细节只以原生 tools 参数承载的 Schema 为准。
16. 所有位置未知的正文、知识卡、混合证据和多跳关系召回统一使用 retrieve_story_context；不要按单一事实或多跳问题选择不同检索工具。名称、别名、存在性与歧义判断使用 resolve_knowledge_identity，明确章节读取仍使用 read_manuscript。
17. 必须通过系统指定的结构化输出 Tool 返回计划，不要在正文中手写 JSON。
"""

_VERIFY_SYSTEM_PROMPT = """你是太初通用写作助手的高层编排 Agent，当前负责结果校验与最终收敛。
请对照用户目标、约束、执行计划和真实节点结果判断任务是否满足。

硬性规则：
1. 不得把失败节点描述为成功，也不得补造节点没有提供的小说事实或来源。
2. satisfied 表示目标已满足；partial 表示可以交付但存在明确缺口；failed 表示没有可用结果。
3. 只有存在可由已注册能力修复的实质缺口时才请求重规划。
4. 最终回答直接面向作者，使用中文，清楚区分事实、建议、草稿与不确定项。
5. 运行记忆不是小说事实；没有重新取证的事实引用不能写成确定事实。
6. 必须通过系统指定的结构化输出 Tool 返回校验结果，不要在正文中手写 JSON。
7. 本次验收记录是当前运行的候选与审查原始产物，不是当前有效小说事实。即使候选被否定或不能再作为事实依据，也必须报告它及其问题，不能因未出现在有效记忆中而忽略。
8. 本次验收记录中的正文候选、审查结论与问题将由程序原样附加到最终回答。你的 final_answer 只总结其他已完成目标（如剧情规划、人物动机、设定与场景安排），不得复制、重写、扩写正文或复述审查结论，不得宣称审查通过或自行修复。节点执行成功不表示审查通过。
9. 用户要求创作并检查时，交付候选及发现的问题即可完成检查目标；只有用户要求修复或目标确有未完成项才重规划。若需要修改候选，须调用真实修改能力并重新审查，不得在最终汇总中暗改。
"""


_PLAN_SYSTEM_PROMPT += """
18. input_bindings 的数组下标统一使用点号，例如 chunks.0.content；方括号形式会被规范化，但不得使用其他路径语法。
19. 重规划时不得重复执行已经成功且仍可满足目标的节点；需要把成功结果带入新修订版时，使用 reuse_from_node_id 明确引用上一修订版节点。
20. 工作记忆不是小说事实；节点状态与结果有效性由 Runtime 管理，不得把失效或被替代结果恢复成当前依据。
21. input_bindings 的 target_path 必须是下游能力输入 Schema 中的真实字段，source_path 必须是上游输出 Schema 中的真实字段；不得凭语义猜测 content、chapters 等字段名。
22. 专业子 Agent 依赖的上游专业产物会由 Runtime 自动写入 source_request.upstream_artifact_refs，作为工作上下文；这不替代能力的必填输入。审查、修改等能力的必填正文参数仍须通过 input_bindings 从上游真实输出字段绑定，不得填产物类型或占位说明。
"""


def _verification_audit(
    run: GeneralAgentRun, *, preview: bool = False
) -> list[dict[str, Any]]:
    """本轮被审对象和审查意见是验收证据，不能复用事实有效性过滤。"""
    current = [
        node
        for node in run.node_runs
        if node.plan_revision == run.plan_revision
        and node.status is GeneralAgentNodeStatus.SUCCESS
    ]
    superseded = {
        dependency
        for node in current
        if node.output.get("artifact_type") == "revision_candidate"
        for dependency in node.dependencies
    }
    audit = []
    for node in current:
        candidate = node.output.get("artifact_type") in {
            "manuscript_candidate",
            "revision_candidate",
        }
        review = "verdict" in node.output and "issues" in node.output
        if not candidate and not review:
            continue
        projected_result = next(
            (
                item
                for item in run.context_pipeline.results.values()
                if item.run_id == run.run_id and item.node_id == node.node_id
            ),
            None,
        )
        audit.append(
            {
                "node_id": node.node_id,
                "usage": "仅作本次验收证据，不能据此恢复为有效小说事实或有效记忆。来源标识全集保存在原始节点产物；此处保留正文、审查问题及问题内的来源引用。",
                "deliver_candidate": candidate and node.node_id not in superseded,
                # 各专家继承的来源标识全集高度重叠，不是验收正文；不要反复投影。
                # 问题自带的 evidence/source_ref、警告与候选正文均完整保留。
                "output": (
                    {
                        "说明": "该产物已纳入全量摘要，需要原文时按引用回读。",
                        "完整结果引用": projected_result.result_ref,
                    }
                    if preview and projected_result and projected_result.full_compacted
                    else projected_result.preview
                    if preview and projected_result
                    else {
                        key: value
                        for key, value in node.output.items()
                        if key != "source_refs"
                    }
                ),
            }
        )
    return audit


class OrchestratorAgent:
    """在 Runtime 之上保持全局控制的高层智能编排者。"""

    def __init__(
        self,
        *,
        llm: BaseChatModel,
        model_router: ModelRoleRouter,
        tool_registry: ToolRegistry,
        subagent_registry: SubagentRegistry,
        trace_repository: InvocationTraceRepository | None = None,
        capability_retrieval_limit: int = 12,
    ) -> None:
        self._llm = llm
        self._model_router = model_router
        self._tool_registry = tool_registry
        self._subagent_registry = subagent_registry
        self._trace_repository = trace_repository
        self._capability_registry = RuntimeCapabilityRegistry(
            tool_registry,
            subagent_registry,
        )
        self._capability_retriever = CapabilityRetriever(
            self._capability_registry,
            limit=capability_retrieval_limit,
        )
        self._schema_loader = ToolSchemaLoader(self._capability_registry)

    async def plan(
        self,
        run: GeneralAgentRun,
        *,
        context: GeneralAgentContextEnvelope,
        replan_guidance: str = "",
    ) -> GeneralAgentExecutionPlan:
        """一次选择能力、填写已知参数并声明结果依赖。"""
        phase = "replan" if replan_guidance else "plan"
        capability_view = self._capability_retriever.retrieve(
            " ".join([context.current_goal, replan_guidance]).strip()
        )
        chapter_orders = explicit_chapter_orders(context.current_goal)
        recent_count = recent_chapter_count(context.current_goal)
        candidate_names = [
            str(item["name"]) for item in capability_view["相关候选摘要"]
        ]
        candidate_tools = self._schema_loader.native_definitions(candidate_names)
        plan_error = ""
        plan: GeneralAgentExecutionPlan | None = None
        for plan_attempt in range(2):
            payload = {
                "允许外部研究": run.external_access_allowed,
                "最大计划节点数": run.limits.max_plan_nodes,
                "当前重规划次数": run.replan_count,
                "已解析的明确章节顺序": chapter_orders,
            }
            if plan_error:
                payload["上一版计划错误"] = plan_error
                payload["修复要求"] = "修复能力、参数或依赖，不要重复上一版错误。"
            candidate = await self._complete_json(
                run=run,
                phase=phase,
                phase_prompt=_PLAN_SYSTEM_PROMPT,
                context=context,
                phase_contract={
                    "相关能力摘要": capability_view["相关候选摘要"],
                    "相关候选数": capability_view["相关候选数"],
                    "能力检索说明": capability_view["说明"],
                },
                working_payload=payload,
                output_schema=GeneralAgentPlanDraft,
                native_tools=candidate_tools,
                output_tool=self._schema_loader.plan_output_tool(
                    GeneralAgentPlanDraft,
                    candidate_names,
                    max_plan_nodes=run.limits.max_plan_nodes,
                    include_unloaded=True,
                ),
            )
            candidate = self._schema_loader.canonicalize_input_aliases(candidate)
            candidate = _apply_recent_chapter_scope(candidate, recent_count)
            try:
                self._validate_capabilities(candidate, run, context=context)
                schema_errors = self._schema_loader.validation_errors(candidate)
                newly_selected = sorted(
                    {node.capability_name for node in candidate.nodes}
                    - set(candidate_names)
                )
                if newly_selected:
                    schema_errors.append(
                        "以下入选能力尚未加载完整契约，须按本次原生契约确认参数与输出绑定："
                        + "、".join(newly_selected)
                    )
                if schema_errors:
                    candidate = await self._materialize_plan(
                        run=run,
                        phase=phase,
                        context=context,
                        draft=candidate,
                        schema_errors=schema_errors,
                    )
                    candidate = self._schema_loader.canonicalize_input_aliases(
                        candidate
                    )
                    candidate = _apply_recent_chapter_scope(candidate, recent_count)
                    self._validate_capabilities(candidate, run, context=context)
                    remaining = self._schema_loader.validation_errors(candidate)
                    if remaining:
                        raise OrchestratorPlanError(
                            "入选能力完整 Schema 校验失败：" + "；".join(remaining)
                        )
            except OrchestratorPlanError as error:
                if plan_attempt:
                    raise
                plan_error = str(error)
                continue
            plan = GeneralAgentExecutionPlan.model_validate(
                candidate.model_dump(mode="json")
            )
            break
        if plan is None:
            raise OrchestratorPlanError("执行计划未通过完整能力契约校验。")
        return plan

    async def compact_context(
        self, run: GeneralAgentRun, *, context: GeneralAgentContextEnvelope
    ) -> None:
        capabilities = self._capability_retriever.retrieve(context.current_goal)
        names = [str(item["name"]) for item in capabilities["相关候选摘要"]]
        await self._complete_json(
            run=run,
            phase="plan",
            phase_prompt=_PLAN_SYSTEM_PROMPT,
            context=context,
            phase_contract={
                "相关能力摘要": capabilities["相关候选摘要"],
                "相关候选数": capabilities["相关候选数"],
                "能力检索说明": capabilities["说明"],
            },
            working_payload={
                "允许外部研究": run.external_access_allowed,
                "最大计划节点数": run.limits.max_plan_nodes,
                "当前重规划次数": run.replan_count,
                "已解析的明确章节顺序": explicit_chapter_orders(context.current_goal),
            },
            output_schema=GeneralAgentPlanDraft,
            native_tools=self._schema_loader.native_definitions(names),
            output_tool=self._schema_loader.plan_output_tool(
                GeneralAgentPlanDraft,
                names,
                max_plan_nodes=run.limits.max_plan_nodes,
                include_unloaded=True,
            ),
            compact_only=True,
        )

    async def _materialize_plan(
        self,
        *,
        run: GeneralAgentRun,
        phase: str,
        context: GeneralAgentContextEnvelope,
        draft: GeneralAgentPlanDraft,
        schema_errors: list[str],
    ) -> GeneralAgentPlanDraft:
        return await self._complete_json(
            run=run,
            phase=f"{phase}.materialize",
            phase_prompt=_PLAN_SYSTEM_PROMPT,
            context=context,
            phase_contract={
                "修复范围": "仅修复已入选能力参数与绑定结构，不扩展能力范围。"
            },
            working_payload={
                "待修复计划": draft.model_dump(mode="json"),
                "Schema校验错误": schema_errors,
            },
            output_schema=GeneralAgentPlanDraft,
            native_tools=self._schema_loader.selected_native_definitions(draft),
            output_tool=self._schema_loader.plan_output_tool(
                GeneralAgentPlanDraft,
                [node.capability_name for node in draft.nodes],
                max_plan_nodes=run.limits.max_plan_nodes,
            ),
        )

    async def verify(
        self,
        run: GeneralAgentRun,
        *,
        context: GeneralAgentContextEnvelope,
    ) -> GeneralAgentVerification:
        """检查真实执行结果并生成最终回答或有限重规划决定。"""
        working_payload = {
            "计划修订号": run.plan_revision,
            "剩余重规划次数": max(0, run.limits.max_replans - run.replan_count),
            "本次验收记录": _verification_audit(run, preview=True),
        }
        decision = await self._complete_json(
            run=run,
            phase="verify",
            phase_prompt=_VERIFY_SYSTEM_PROMPT,
            context=context,
            phase_contract={},
            working_payload=working_payload,
            output_schema=GeneralAgentVerification,
            native_tools=[],
        )
        if run.replan_count >= run.limits.max_replans and decision.should_replan:
            decision = decision.model_copy(update={"should_replan": False})
        if decision.should_replan:
            return decision
        return decision.model_copy(
            update={
                "final_answer": compose_verified_delivery(
                    decision.final_answer,
                    _verification_audit(run),
                ),
            }
        )

    def _validate_capabilities(
        self,
        plan: GeneralAgentExecutionPlan,
        run: GeneralAgentRun,
        *,
        context: GeneralAgentContextEnvelope,
    ) -> None:
        tools = {item.name: item for item in self._tool_registry.list_manifests()}
        subagents = {
            item.name: item for item in self._subagent_registry.list_manifests()
        }
        if len(plan.nodes) > run.limits.max_plan_nodes:
            raise OrchestratorPlanError("编排计划超过本次任务允许的节点数量。")
        for node in plan.nodes:
            invalid_artifact_refs = _invalid_literal_artifact_refs(node.input_data)
            if invalid_artifact_refs:
                raise OrchestratorPlanError(
                    f"节点“{node.node_id}”提供了无效的中间产物 ID："
                    + "、".join(invalid_artifact_refs)
                    + "。节点间产物必须通过 dependencies 或 input_bindings 传递。"
                )
            if node.reuse_from_node_id is not None:
                reusable = [
                    item
                    for item in run.node_runs
                    if item.node_id == node.reuse_from_node_id
                    and item.status is GeneralAgentNodeStatus.SUCCESS
                ]
                if not reusable:
                    raise OrchestratorPlanError(
                        f"计划要求复用的成功节点“{node.reuse_from_node_id}”不存在。"
                    )
                source = max(reusable, key=lambda item: item.plan_revision)
                if (
                    source.kind is not node.kind
                    or source.capability_name != node.capability_name
                ):
                    raise OrchestratorPlanError(
                        f"节点“{node.node_id}”与复用来源"
                        f"“{node.reuse_from_node_id}”的能力契约不一致。"
                    )
            if node.kind is GeneralAgentNodeKind.TOOL:
                manifest = tools.get(node.capability_name)
                if manifest is None:
                    raise OrchestratorPlanError(
                        f"编排计划引用了未知工具“{node.capability_name}”。"
                    )
                if (
                    manifest.requires_external_access
                    and not run.external_access_allowed
                ):
                    raise OrchestratorPlanError(
                        "用户未允许外部研究，计划却安排了外部工具。"
                    )
                continue
            if node.capability_name not in subagents:
                raise OrchestratorPlanError(
                    f"编排计划引用了未知专业子智能体“{node.capability_name}”。"
                )
            if (
                node.capability_name == "external_research"
                and not run.external_access_allowed
            ):
                raise OrchestratorPlanError(
                    "用户未允许外部研究，计划却安排了外部研究。"
                )

        if not is_explicit_chapter_content_request(context.current_goal):
            return
        if "read_manuscript" not in tools:
            return
        if not plan.nodes:
            raise OrchestratorPlanError(
                "明确章节内容请求不能直接回答，必须先用 read_manuscript 读取正文。"
            )
        selected_names = {node.capability_name for node in plan.nodes}
        if "read_manuscript" not in selected_names:
            orders = explicit_chapter_orders(context.current_goal)
            raise OrchestratorPlanError(
                "请求已明确指定章节顺序"
                f"{orders}，必须选择 read_manuscript 直接读取正文，"
                "不得降级为相关性检索或只调用事实证据子智能体。"
            )

    async def _complete_json(
        self,
        *,
        run: GeneralAgentRun,
        phase: str,
        phase_prompt: str,
        context: GeneralAgentContextEnvelope,
        phase_contract: dict[str, Any],
        working_payload: dict[str, Any],
        output_schema: type[_OutputModel],
        native_tools: list[dict[str, Any]],
        output_tool: dict[str, Any] | None = None,
        compact_only: bool = False,
    ) -> _OutputModel:
        model_id = run.model_id or self._model_router.model_for("orchestrator")
        system_memory = _json_message(
            {
                "稳定记忆（System Prompt）": {
                    "身份、基本行为与准则": context.stable_memory,
                    "Static Capability Index（静态能力索引）": (
                        self._capability_registry.lightweight_index()
                    ),
                }
            }
        )
        long_term_prompt = _json_message(
            {
                "长期记忆": [
                    item.model_dump(mode="json") for item in context.long_term_memory
                ]
            }
        )
        history_summary_prompt = _json_message(
            {"历史对话摘要": context.history_memory.summary}
        )
        working_prompt = _json_message(
            {
                "工作记忆": context.working_memory.model_dump(mode="json"),
                "阶段行为与准则": phase_prompt,
                "阶段契约": phase_contract,
                "本阶段运行参数": working_payload,
                "当前请求附加上下文": {
                    "用户约束": context.current_request.user_constraints,
                    "作用范围": context.current_request.scope,
                },
            }
        )
        actual_output_tool = output_tool or _structured_output_tool(output_schema)
        output_tool_name = str(actual_output_tool["function"]["name"])
        parser = PydanticToolsParser(tools=[output_schema], first_tool_only=True)
        task_name = f"general_writing_orchestrator.{phase}"
        trace_invocation = InvocationContext(
            task_id=run.task_id,
            run_id=run.run_id,
            caller_type="orchestrator",
            caller_name="general_writing_orchestrator",
            phase=phase,
        )
        agent = create_agent(
            model=self._llm,
            tools=[*native_tools, actual_output_tool],
            system_prompt=SystemMessage(content=system_memory),
            middleware=[
                NamedToolChoiceMiddleware(output_tool_name),
                ModelRequestSettingsMiddleware(
                    model_id=model_id,
                    task_type="general_agent_orchestration",
                    task_name=task_name,
                    taichu_run_id=run.run_id,
                    context_snapshot_id=run.context_snapshot_id,
                    chapter_ids=tuple(run.scope.chapter_ids),
                    temperature=0.1,
                    max_output_tokens=12_000,
                    feature="general_writing_assistant",
                ),
                ContextPipelineMiddleware(),
                ModelInvocationTraceMiddleware(
                    repository=self._trace_repository,
                    invocation=trace_invocation,
                    requested_model_id=model_id,
                    model_role="orchestrator",
                    capability_name=task_name,
                ),
            ],
            name="general_writing_orchestrator",
        )
        last_raw = ""
        last_error: Exception | None = None
        for attempt in range(2):
            working_messages = [
                ChatMessage(
                    role="developer",
                    content=working_prompt,
                    additional_kwargs={"context_layer": "working_memory"},
                ),
            ]
            if attempt:
                working_messages.append(
                    ChatMessage(
                        role="developer",
                        content=(
                            "上次结构化 Tool 参数未通过校验，请只修复参数。"
                            f"\n错误：{str(last_error)[:2_000]}"
                            f"\n上次 Tool 参数：{last_raw[:10_000]}"
                        ),
                    )
                )
            messages: list[AnyMessage] = [
                ChatMessage(role="developer", content=long_term_prompt),
                ChatMessage(
                    role="developer",
                    content=history_summary_prompt,
                    additional_kwargs={"context_layer": "history_summary"},
                ),
                *[
                    HumanMessage(
                        content=message.content,
                        id=message_id(message),
                        additional_kwargs={"context_layer": "history_raw"},
                    )
                    if message.role == "user"
                    else AIMessage(
                        content=message.content,
                        id=message_id(message),
                        additional_kwargs={"context_layer": "history_raw"},
                    )
                    for message in context.history_memory.messages
                    if message.role in {"user", "assistant"}
                ],
                *working_messages,
                HumanMessage(content=context.current_request.content),
                *[
                    HumanMessage(content=response)
                    for response in context.current_request.human_responses
                ],
            ]
            if compact_only:
                scope = ACTIVE_PIPELINE.get()
                if scope is None:
                    raise ValueError("手动压缩缺少主 Agent 上下文作用域。")
                await scope.engine.before_model(
                    scope,
                    ModelRequest(
                        model=self._llm,
                        messages=messages,
                        system_message=SystemMessage(content=system_memory),
                        tools=[*native_tools, actual_output_tool],
                        model_settings={
                            "model_id": model_id,
                            "max_output_tokens": 12_000,
                        },
                    ),
                )
                return cast(_OutputModel, None)
            try:
                agent_input: InputAgentState = {
                    "messages": cast(Any, messages),
                }
                agent_config: RunnableConfig = {
                    "run_name": task_name,
                    "metadata": {
                        "taichu_run_id": run.run_id,
                        "context_snapshot_id": run.context_snapshot_id,
                        "model_role": "orchestrator",
                    },
                }
                result = await agent.ainvoke(
                    agent_input,
                    config=agent_config,
                )
                response = next(
                    (
                        message
                        for message in reversed(result["messages"])
                        if isinstance(message, AIMessage)
                    ),
                    None,
                )
                if not isinstance(response, AIMessage):
                    raise OrchestratorOutputError("模型没有返回 AIMessage。")
                last_raw = _raw_output_text(response)
                output = await parser.ainvoke(response)
                if not isinstance(output, output_schema):
                    raise OrchestratorOutputError("模型没有调用指定的结构化输出 Tool。")
                return output
            except ContextCapacityError:
                raise
            except (ValidationError, ValueError, OutputParserException) as error:
                last_error = error
        raise OrchestratorOutputError(
            f"高层编排 Agent 输出未通过结构校验：{last_error}"
        )


_ARTIFACT_ID = re.compile(r"^artifact_[a-f0-9]{32}$")


def _invalid_literal_artifact_refs(input_data: dict[str, Any]) -> tuple[str, ...]:
    source_request = input_data.get("source_request")
    if not isinstance(source_request, dict):
        return ()
    refs = source_request.get("upstream_artifact_refs")
    if not isinstance(refs, list):
        return ()
    return tuple(
        str(ref)
        for ref in refs
        if not isinstance(ref, str) or _ARTIFACT_ID.fullmatch(ref) is None
    )


def _apply_recent_chapter_scope(
    plan: GeneralAgentPlanDraft,
    count: int | None,
) -> GeneralAgentPlanDraft:
    """把“最近 N 章”落实为 Tool 正式参数，不让模型解析卷章树数组。"""

    if count is None:
        return plan
    changed = False
    nodes = []
    range_fields = {
        "chapter_ids",
        "start_order",
        "end_order",
        "recent_count",
    }
    for node in plan.nodes:
        if node.capability_name != "read_manuscript" or "最近" not in node.objective:
            nodes.append(node)
            continue
        input_data = {
            key: value
            for key, value in node.input_data.items()
            if key not in range_fields
        }
        input_data["recent_count"] = count
        input_bindings = [
            binding
            for binding in node.input_bindings
            if binding.target_path.split(".", 1)[0] not in range_fields
        ]
        nodes.append(
            node.model_copy(
                update={
                    "input_data": input_data,
                    "input_bindings": input_bindings,
                }
            )
        )
        changed = True
    if not changed:
        return plan
    return plan.model_copy(update={"nodes": nodes})


def _json_message(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _structured_output_tool(
    schema: type[BaseModel],
) -> dict[str, Any]:
    return convert_to_openai_tool(schema, strict=True)


def _raw_output_text(message: AIMessage) -> str:
    return json.dumps(
        {
            "content": message.content,
            "tool_calls": message.tool_calls,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


class OrchestratorPlanError(ValueError):
    """高层计划引用了无效能力或违反任务权限。"""


class OrchestratorOutputError(ValueError):
    """高层编排 Agent 未能产出有效结构化结果。"""
