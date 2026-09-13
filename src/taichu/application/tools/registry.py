"""校验、注册和通过统一协议调用可复用 Tool。"""

import asyncio
import hashlib
import logging
from time import perf_counter
from collections.abc import Callable, Iterable
from typing import Annotated
from uuid import uuid4

from langchain.tools import ToolRuntime
from langchain_core.tools import (
    BaseTool,
    InjectedToolCallId,
    StructuredTool,
)
from pydantic import BaseModel

from taichu.application.capabilities import CapabilityContext
from taichu.application.contracts.invocation_trace import (
    InvocationTraceRepository,
)
from taichu.application.contracts.general_agent_tool_budget import (
    GeneralAgentToolBudgetClaim,
    GeneralAgentToolBudgetOwner,
    GeneralAgentToolBudgetRepository,
)
from taichu.application.invocations.models import (
    InvocationContext,
    InvocationEnvelope,
    InvocationStatus,
    InvocationTraceRecord,
    now_iso,
)
from taichu.application.services.invocation_policy_service import (
    InvocationPolicyService,
    canonical_input_hash,
)
from taichu.application.tools.contract import (
    ToolAuthorizationPolicy,
    ToolIdempotencyPolicy,
    ToolManifest,
    ToolPlugin,
    ToolReconciliationResult,
    ToolReconciliationStatus,
    ToolSideEffect,
    langchain_agent_args_schema,
    langchain_direct_args_schema,
)

logger = logging.getLogger(__name__)


class ToolRegistry:
    """管理已通过协议校验的 Tool 插件。"""

    def __init__(
        self,
        context: CapabilityContext,
        trace_repository: InvocationTraceRepository | None = None,
        *,
        tool_budget_repository: GeneralAgentToolBudgetRepository | None = None,
        require_tool_budget: bool = False,
    ) -> None:
        self._context = context
        self._plugins: dict[str, ToolPlugin] = {}
        self._trace_repository = trace_repository
        self._tool_budget_repository = tool_budget_repository
        self._require_tool_budget = require_tool_budget

    def register(self, plugin: ToolPlugin) -> None:
        """校验并注册单个 Tool。"""
        name = plugin.manifest.name
        if name in self._plugins:
            raise ToolRegistrationError(f"Tool '{name}' is already registered")

        missing = (
            plugin.manifest.required_capabilities - self._context.capabilities.keys()
        )
        if missing:
            raise ToolRegistrationError(
                f"工具“{name}”缺少所需能力：{', '.join(sorted(missing))}"
            )
        if (
            plugin.manifest.side_effect
            in {ToolSideEffect.WRITE, ToolSideEffect.HIGH_RISK_WRITE}
            and plugin.reconcile is None
        ):
            raise ToolRegistrationError(
                f"写入工具“{name}”必须注册真实资源副作用对账器。"
            )

        self._plugins[name] = plugin

    def register_all(self, plugins: Iterable[ToolPlugin]) -> None:
        """注册一组 Tool 候选。"""
        for plugin in plugins:
            self.register(plugin)

    def list_manifests(self) -> list[ToolManifest]:
        """按名称返回所有已注册 Tool 的元信息。"""
        return [self._plugins[name].manifest for name in sorted(self._plugins)]

    def get_manifest(self, name: str) -> ToolManifest:
        """取得 Tool Manifest，不暴露可绕过门禁的原始 Handler。"""
        if name not in self._plugins:
            raise ToolNotFoundError(name)
        return self._plugins[name].manifest

    def bind_langchain_tool(
        self,
        name: str,
        invocation: InvocationContext,
        *,
        result_sink: Callable[[InvocationEnvelope[BaseModel]], None] | None = None,
    ) -> BaseTool:
        """把已注册能力绑定为本次调用作用域内的官方 LangChain Tool。"""

        manifest = self.get_manifest(name)

        async def call_tool(
            tool_call_id: Annotated[str, InjectedToolCallId],
            **input_data: object,
        ) -> tuple[str, object]:
            effective_invocation = invocation.model_copy(
                update={"call_id": tool_call_id}
            )
            envelope = await self.invoke(name, input_data, effective_invocation)
            if result_sink is not None:
                result_sink(envelope)
            return envelope.output.model_dump_json(), envelope

        return StructuredTool.from_function(
            coroutine=call_tool,
            name=manifest.name,
            description=manifest.description,
            args_schema=langchain_direct_args_schema(manifest.input_schema),
            response_format="content_and_artifact",
            metadata={
                "side_effect": manifest.side_effect.value,
                "authorization_policy": manifest.authorization_policy.value,
            },
        )

    def bind_langchain_agent_tool(
        self,
        name: str,
        invocation: InvocationContext,
        *,
        result_sink: Callable[[InvocationEnvelope[BaseModel]], None] | None = None,
    ) -> BaseTool:
        """绑定由 Agent ToolNode 执行、使用官方 ToolRuntime 身份的 Tool。"""

        manifest = self.get_manifest(name)

        async def call_tool(
            runtime: ToolRuntime[None, dict[str, object]],
            **input_data: object,
        ) -> tuple[str, object]:
            tool_call_id = runtime.tool_call_id
            if not tool_call_id:
                raise ToolInvocationError(
                    "Agent Tool 调用缺少 LangChain tool_call_id。"
                )
            effective_invocation = invocation.model_copy(
                update={"call_id": tool_call_id}
            )
            envelope = await self.invoke(name, input_data, effective_invocation)
            if result_sink is not None:
                result_sink(envelope)
            return envelope.output.model_dump_json(), envelope

        return StructuredTool.from_function(
            coroutine=call_tool,
            name=manifest.name,
            description=manifest.description,
            args_schema=langchain_agent_args_schema(manifest.input_schema),
            response_format="content_and_artifact",
            metadata={
                "side_effect": manifest.side_effect.value,
                "authorization_policy": manifest.authorization_policy.value,
            },
        )

    async def invoke(
        self,
        name: str,
        input_data: BaseModel | dict[str, object],
        invocation: InvocationContext,
    ) -> InvocationEnvelope[BaseModel]:
        """统一执行 Schema、权限、幂等、超时、结果和技术日志门禁。"""
        if name not in self._plugins:
            raise ToolNotFoundError(name)
        plugin = self._plugins[name]
        manifest = plugin.manifest
        parsed_input = manifest.input_schema.model_validate(input_data)
        self._validate_caller(manifest, invocation)
        await self._claim_tool_budget(manifest.name, parsed_input, invocation)
        policy = self._policy_service()
        authorization_reference: str | None = None
        if manifest.requires_external_access:
            authorization_reference = await policy.authorize_external(
                grant_id=invocation.external_access_grant_id,
                task_id=invocation.task_id,
                tool_name=name,
            )

        idempotency_key = _string_field(parsed_input, "idempotency_key")
        if manifest.idempotency_policy is ToolIdempotencyPolicy.REQUIRED:
            if not idempotency_key:
                raise ToolInvocationError("写入工具必须提供幂等键。")
            existing = await policy.get_idempotent_result(
                tool_name=name,
                idempotency_key=idempotency_key,
                input_payload=parsed_input,
            )
            if existing is not None:
                output = manifest.output_schema.model_validate(existing)
                return await self._completed_envelope(
                    manifest=manifest,
                    invocation=invocation,
                    parsed_input=parsed_input,
                    output=output,
                    started_at=now_iso(),
                    timer=perf_counter(),
                    authorization_reference=_string_field(
                        parsed_input,
                        "author_grant_id",
                    ),
                )

        if manifest.authorization_policy is not ToolAuthorizationPolicy.NONE:
            authorization_reference = await policy.authorize_write(
                grant_id=_string_field(parsed_input, "author_grant_id"),
                task_id=invocation.task_id,
                tool_name=name,
                input_payload=parsed_input,
                require_second_confirmation=(
                    manifest.authorization_policy
                    is ToolAuthorizationPolicy.SECOND_CONFIRMATION
                ),
            )

        started_at = now_iso()
        timer = perf_counter()
        try:
            async with asyncio.timeout(manifest.default_timeout_seconds):
                raw_output = await plugin.run(
                    parsed_input,
                    invocation,
                    self._context,
                )
            output = manifest.output_schema.model_validate(raw_output)
            if (
                manifest.idempotency_policy is ToolIdempotencyPolicy.REQUIRED
                and idempotency_key is not None
            ):
                await policy.save_idempotent_result(
                    tool_name=name,
                    idempotency_key=idempotency_key,
                    input_payload=parsed_input,
                    output=output,
                )
            return await self._completed_envelope(
                manifest=manifest,
                invocation=invocation,
                parsed_input=parsed_input,
                output=output,
                started_at=started_at,
                timer=timer,
                authorization_reference=authorization_reference,
            )
        except TimeoutError as error:
            await self._append_failure_trace(
                manifest,
                invocation,
                parsed_input,
                started_at,
                timer,
                InvocationStatus.TIMED_OUT,
                authorization_reference,
                error,
            )
            raise ToolInvocationTimeoutError(name) from error
        except Exception as error:
            await self._append_failure_trace(
                manifest,
                invocation,
                parsed_input,
                started_at,
                timer,
                InvocationStatus.FAILED,
                authorization_reference,
                error,
            )
            raise

    async def reconcile(
        self,
        name: str,
        input_data: BaseModel | dict[str, object],
        invocation: InvocationContext,
    ) -> ToolReconciliationResult:
        """由 Tool 自身只读核对真实副作用，不复用运行时分支判断。"""
        if name not in self._plugins:
            raise ToolNotFoundError(name)
        plugin = self._plugins[name]
        manifest = plugin.manifest
        parsed_input = manifest.input_schema.model_validate(input_data)
        self._validate_caller(manifest, invocation)
        if plugin.reconcile is None:
            return ToolReconciliationResult(
                status=ToolReconciliationStatus.UNKNOWN,
                reason="该写入工具没有注册副作用对账器。",
            )
        result = await plugin.reconcile(parsed_input, invocation, self._context)
        if result.status is not ToolReconciliationStatus.SUCCEEDED:
            return result
        output = manifest.output_schema.model_validate(result.output)
        idempotency_key = _string_field(parsed_input, "idempotency_key")
        if (
            manifest.idempotency_policy is ToolIdempotencyPolicy.REQUIRED
            and idempotency_key is not None
        ):
            await self._policy_service().save_idempotent_result(
                tool_name=name,
                idempotency_key=idempotency_key,
                input_payload=parsed_input,
                output=output,
            )
        return result.model_copy(update={"output": output.model_dump(mode="json")})

    def _validate_caller(
        self,
        manifest: ToolManifest,
        invocation: InvocationContext,
    ) -> None:
        allowed = manifest.allowed_callers
        if (
            invocation.caller_name not in allowed
            and invocation.caller_type not in allowed
        ):
            raise ToolInvocationPermissionError(
                f"调用方“{invocation.caller_name}”无权使用工具“{manifest.name}”。"
            )

    async def _claim_tool_budget(
        self,
        tool_name: str,
        parsed_input: BaseModel,
        invocation: InvocationContext,
    ) -> None:
        """在任何授权或 Handler 行为前占用任务级共享预算。"""

        if invocation.conversation_id is None or invocation.caller_type not in {
            "orchestrator",
            "subagent",
        }:
            return
        repository = self._tool_budget_repository
        if repository is None:
            if self._require_tool_budget:
                raise ToolInvocationError("通用 Agent Tool 调用缺少任务级预算仓储。")
            return
        owner = GeneralAgentToolBudgetOwner(
            conversation_id=invocation.conversation_id,
            run_id=invocation.run_id,
        )
        await repository.initialize(owner, invocation.budget.max_tool_calls)
        await repository.claim(
            owner,
            GeneralAgentToolBudgetClaim(
                logical_call_id=_logical_tool_call_id(invocation),
                tool_name=tool_name,
                input_sha256=canonical_input_hash(parsed_input),
                call_id=invocation.call_id,
                parent_call_id=invocation.parent_call_id,
                caller_name=invocation.caller_name,
                phase=invocation.phase,
                claimed_at=now_iso(),
            ),
        )

    def _policy_service(self) -> InvocationPolicyService:
        return self._context.require(
            "invocation_policy_service",
            InvocationPolicyService,
        )

    async def _completed_envelope(
        self,
        *,
        manifest: ToolManifest,
        invocation: InvocationContext,
        parsed_input: BaseModel,
        output: BaseModel,
        started_at: str,
        timer: float,
        authorization_reference: str | None,
    ) -> InvocationEnvelope[BaseModel]:
        finished_at = now_iso()
        duration_ms = max(0, round((perf_counter() - timer) * 1000))
        trace_id = f"trace_{uuid4().hex}"
        source_refs = _source_refs(output)
        await self._append_trace(
            InvocationTraceRecord(
                trace_id=trace_id,
                capability_type="tool",
                capability_name=manifest.name,
                task_id=invocation.task_id,
                run_id=invocation.run_id,
                call_id=invocation.call_id,
                parent_call_id=invocation.parent_call_id,
                caller_type=invocation.caller_type,
                caller_name=invocation.caller_name,
                status=InvocationStatus.COMPLETED,
                input_sha256=canonical_input_hash(parsed_input),
                input_char_count=len(parsed_input.model_dump_json()),
                output_char_count=len(output.model_dump_json()),
                source_count=len(source_refs),
                side_effect=manifest.side_effect,
                authorization_reference=authorization_reference,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
            )
        )
        return InvocationEnvelope[BaseModel](
            invocation_id=invocation.call_id,
            capability_type="tool",
            capability_name=manifest.name,
            status=InvocationStatus.COMPLETED,
            output=output,
            source_refs=source_refs,
            trace_id=trace_id,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
        )

    async def _append_failure_trace(
        self,
        manifest: ToolManifest,
        invocation: InvocationContext,
        parsed_input: BaseModel,
        started_at: str,
        timer: float,
        status: InvocationStatus,
        authorization_reference: str | None,
        error: Exception,
    ) -> None:
        await self._append_trace(
            InvocationTraceRecord(
                trace_id=f"trace_{uuid4().hex}",
                capability_type="tool",
                capability_name=manifest.name,
                task_id=invocation.task_id,
                run_id=invocation.run_id,
                call_id=invocation.call_id,
                parent_call_id=invocation.parent_call_id,
                caller_type=invocation.caller_type,
                caller_name=invocation.caller_name,
                status=status,
                input_sha256=canonical_input_hash(parsed_input),
                input_char_count=len(parsed_input.model_dump_json()),
                side_effect=manifest.side_effect,
                authorization_reference=authorization_reference,
                started_at=started_at,
                finished_at=now_iso(),
                duration_ms=max(0, round((perf_counter() - timer) * 1000)),
                error_type=type(error).__name__,
                error_message=str(error)[:500],
            )
        )

    async def _append_trace(self, record: InvocationTraceRecord) -> None:
        if self._trace_repository is None:
            return
        try:
            await self._trace_repository.append(record)
        except Exception:  # noqa: BLE001
            logger.exception("Tool 调用轨迹写入失败；主调用结果不受影响。")


class ToolRegistrationError(ValueError):
    """Tool 未通过注册校验。"""


class ToolNotFoundError(LookupError):
    """请求的 Tool 不存在。"""

    def __init__(self, name: str) -> None:
        super().__init__(f"工具“{name}”不存在")


class ToolInvocationError(RuntimeError):
    """Tool 调用未能满足统一技术契约。"""


class ToolInvocationPermissionError(PermissionError):
    """调用方不在 Tool 的最小权限集合中。"""


class ToolInvocationTimeoutError(TimeoutError):
    """Tool 超过 Manifest 声明的执行时限。"""

    def __init__(self, name: str) -> None:
        super().__init__(f"工具“{name}”执行超时。")


def _string_field(model: BaseModel, name: str) -> str | None:
    value = getattr(model, name, None)
    return value if isinstance(value, str) and value else None


def _source_refs(output: BaseModel) -> list[str]:
    value = getattr(output, "source_refs", None)
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    retrieval_id = getattr(output, "retrieval_id", None)
    if isinstance(retrieval_id, str) and retrieval_id:
        return [retrieval_id]
    return []


def _logical_tool_call_id(invocation: InvocationContext) -> str:
    identity = (f"{invocation.parent_call_id or ''}\0{invocation.call_id}").encode(
        "utf-8"
    )
    return f"tool_budget_call_{hashlib.sha256(identity).hexdigest()}"
