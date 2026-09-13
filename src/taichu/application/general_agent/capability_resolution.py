"""通用 Agent 的能力索引检索、按需 Schema 加载与计划输入校验。"""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import json
import re
from typing import Any, TypeVar

from langchain_core.tools import StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, TypeAdapter, ValidationError

from taichu.application.general_agent.models import (
    GeneralAgentExecutionPlan,
    GeneralAgentNodeKind,
    GeneralAgentPlanNode,
)
from taichu.application.subagents.registry import SubagentRegistry
from taichu.application.tools.contract import (
    RUNTIME_INPUT_FIELDS,
    langchain_args_schema,
)
from taichu.application.tools.registry import ToolRegistry

_CORE_CAPABILITIES = {
    "read_runtime_result",
    "retrieve_story_context",
    "read_manuscript",
    "resolve_knowledge_identity",
    "canon_evidence",
}
_WORD = re.compile(r"[a-z0-9_]+|[\u3400-\u9fff]", re.I)


@dataclass(frozen=True, slots=True)
class CapabilityContract:
    """从真实 Registry 加载的单项完整能力契约。"""

    name: str
    kind: GeneralAgentNodeKind
    description: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel] | None = None


class RuntimeCapabilityRegistry:
    """聚合 Tool/Subagent Registry，但不复制它们的注册职责。"""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        subagent_registry: SubagentRegistry,
    ) -> None:
        self._tools = tool_registry
        self._subagents = subagent_registry

    def lightweight_index(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for tool_manifest in self._tools.list_manifests():
            result.append(
                {
                    "name": tool_manifest.name,
                    "type": "tool",
                    "description": tool_manifest.description,
                    "side_effect": tool_manifest.side_effect.value,
                    "requires_external_access": (
                        tool_manifest.requires_external_access
                    ),
                    "authorization_policy": (tool_manifest.authorization_policy.value),
                }
            )
        for subagent_manifest in self._subagents.list_manifests():
            result.append(
                {
                    "name": subagent_manifest.name,
                    "type": "subagent",
                    "label": subagent_manifest.label,
                    "description": subagent_manifest.description,
                    "non_responsibilities": list(
                        subagent_manifest.non_responsibilities
                    ),
                    "accepted_artifact_types": sorted(
                        subagent_manifest.accepted_artifact_types
                    ),
                    "produced_artifact_types": sorted(subagent_manifest.artifact_types),
                }
            )
        return sorted(result, key=lambda item: (str(item["type"]), str(item["name"])))

    def load(self, kind: GeneralAgentNodeKind, name: str) -> CapabilityContract:
        if kind is GeneralAgentNodeKind.TOOL:
            manifest = self._tools.get_manifest(name)
            return CapabilityContract(
                name=name,
                kind=kind,
                description=manifest.description,
                input_schema=manifest.input_schema,
                output_schema=manifest.output_schema,
            )
        subagent_manifest = self._subagents.get_manifest(name)
        return CapabilityContract(
            name=name,
            kind=kind,
            description=subagent_manifest.description,
            input_schema=subagent_manifest.input_schema,
            output_schema=subagent_manifest.output_schema,
        )

    def load_by_name(self, name: str) -> CapabilityContract:
        tool_names = {item.name for item in self._tools.list_manifests()}
        if name in tool_names:
            return self.load(GeneralAgentNodeKind.TOOL, name)
        return self.load(GeneralAgentNodeKind.SUBAGENT, name)


class CapabilityRetriever:
    """在完整轻索引中排序候选，但不把 Schema 注入提示词。"""

    def __init__(self, registry: RuntimeCapabilityRegistry, *, limit: int = 12) -> None:
        self._registry = registry
        self._limit = max(4, limit)

    def retrieve(self, query: str) -> dict[str, Any]:
        index = self._registry.lightweight_index()
        query_terms = _terms(query)
        ranked = sorted(
            index,
            key=lambda item: (
                -_relevance(query_terms, _search_text(item)),
                str(item["type"]),
                str(item["name"]),
            ),
        )
        selected_names = {
            str(item["name"]) for item in ranked[: self._limit]
        } | _CORE_CAPABILITIES
        candidates: list[dict[str, Any]] = []
        for item in index:
            name = str(item["name"])
            if name not in selected_names:
                continue
            candidates.append(dict(item))
        return {
            "能力总数": len(index),
            "完整轻量索引": index,
            "相关候选数": len(candidates),
            "相关候选摘要": candidates,
            "说明": (
                "轻量索引中的能力均真实注册；候选能力的输入 Schema 仅通过"
                "模型 API 原生 tools 参数传递，不进入任何提示词消息。"
                "候选只是优先加载的契约，不是能力白名单；完整索引中的其他能力仍可入选，"
                "其完整契约会在入选后加载并校验。"
            ),
        }


class ToolSchemaLoader:
    """只加载计划实际使用的完整契约，并在 Executor 前检查输入边界。"""

    def __init__(self, registry: RuntimeCapabilityRegistry) -> None:
        self._registry = registry

    def native_definitions(self, names: list[str]) -> list[dict[str, Any]]:
        """把候选能力 Schema 装进模型 API 的原生 ``tools`` 参数。"""
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            contract = self._registry.load_by_name(name)
            result.append(_native_tool_definition(contract))
        return result

    def selected_native_definitions(
        self, plan: GeneralAgentExecutionPlan
    ) -> list[dict[str, Any]]:
        return self.native_definitions([node.capability_name for node in plan.nodes])

    def plan_output_tool(
        self,
        schema: type[BaseModel],
        names: list[str],
        *,
        max_plan_nodes: int,
        include_unloaded: bool = False,
    ) -> dict[str, Any]:
        """在原生计划输出中按能力约束已知参数，绑定值仍由执行前校验负责。"""

        # 严格模式会把动态参数字典变成不允许任何字段的空对象，且会强制
        # 补齐本应省略的可选参数。这里保留官方非严格 Tool Schema 语义。
        tool = convert_to_openai_tool(schema, strict=False)
        nodes = tool["function"]["parameters"]["properties"]["nodes"]
        template = nodes["items"]
        variants = []
        for name in dict.fromkeys(names):
            contract = self._registry.load_by_name(name)
            definition = _native_tool_definition(contract)["function"]
            variant = deepcopy(template)
            variant["description"] = definition["description"]
            properties = variant["properties"]
            properties["kind"] = {"type": "string", "enum": [contract.kind.value]}
            properties["capability_name"] = {"type": "string", "enum": [name]}
            known_input = deepcopy(definition["parameters"])
            # 必填字段可以由 input_bindings 提供，不能强迫模型填占位文本。
            # 仅移除输入根对象的 required，嵌套字段的类型与必填约束不变。
            known_input.pop("required", None)
            known_input["additionalProperties"] = False
            known_input["description"] = (
                "只填写当前已知的真实参数；由上游提供的字段省略并声明 input_bindings，"
                "不要填写占位文本、字段名或产物类型来代替结果。"
            )
            properties["input_data"] = known_input
            variants.append(variant)
        if include_unloaded:
            # 检索排序只决定预加载哪些契约，不能裁掉已注册能力的可选范围。
            # 未预加载的能力先保留参数字典，入选后再用真实契约具体化。
            for kind in GeneralAgentNodeKind:
                unloaded = [
                    str(item["name"])
                    for item in self._registry.lightweight_index()
                    if item["type"] == kind.value and item["name"] not in names
                ]
                if not unloaded:
                    continue
                variant = deepcopy(template)
                variant["description"] = (
                    "完整索引中尚未加载详细契约的已注册能力；按目标选择，"
                    "完整参数和输出契约会在入选后加载，不能因未预加载而省略用户目标。"
                )
                variant["properties"]["kind"] = {"type": "string", "enum": [kind.value]}
                variant["properties"]["capability_name"] = {
                    "type": "string",
                    "enum": unloaded,
                }
                variant["properties"]["input_data"] = {
                    "type": "object",
                    "additionalProperties": True,
                }
                variants.append(variant)
        nodes["maxItems"] = max_plan_nodes
        nodes["items"] = {"anyOf": variants} if variants else {"not": {}}
        return dict(tool)

    def validation_errors(self, plan: GeneralAgentExecutionPlan) -> list[str]:
        errors: list[str] = []
        nodes = {node.node_id: node for node in plan.nodes}
        for node in plan.nodes:
            try:
                contract = self._registry.load(node.kind, node.capability_name)
            except LookupError as error:
                errors.append(f"节点 {node.node_id}：{error}")
                continue
            errors.extend(_validate_node_input(node, contract))
            for binding in node.input_bindings:
                source_node = nodes.get(binding.source_node_id)
                if source_node is None:
                    continue
                try:
                    source_contract = self._registry.load(
                        source_node.kind,
                        source_node.capability_name,
                    )
                except LookupError:
                    continue
                errors.extend(
                    _validate_binding_contract(
                        node,
                        binding.source_path,
                        binding.target_path,
                        source_node.node_id,
                        source_contract,
                        contract,
                    )
                )
        return errors

    def canonicalize_input_aliases(
        self,
        plan: _PlanT,
    ) -> _PlanT:
        """只把可由真实 Schema 唯一确定的常见范围别名改成正式字段。"""

        changed = False
        nodes: list[GeneralAgentPlanNode] = []
        for node in plan.nodes:
            try:
                contract = self._registry.load(node.kind, node.capability_name)
            except LookupError:
                nodes.append(node)
                continue
            input_data = _canonicalize_range_aliases(
                node.input_data,
                set(contract.input_schema.model_fields),
            )
            if input_data == node.input_data:
                nodes.append(node)
                continue
            changed = True
            nodes.append(node.model_copy(update={"input_data": input_data}))
        if not changed:
            return plan
        return plan.model_copy(update={"nodes": nodes})


_PlanT = TypeVar("_PlanT", bound=GeneralAgentExecutionPlan)


def _validate_node_input(
    node: GeneralAgentPlanNode,
    contract: CapabilityContract,
) -> list[str]:
    fields = contract.input_schema.model_fields
    errors: list[str] = []
    unknown = sorted(set(node.input_data) - set(fields))
    if unknown:
        allowed = _planning_field_names(fields)
        errors.append(
            f"节点 {node.node_id} 的 input_data 包含未知字段：{', '.join(unknown)}；"
            f"允许字段：{', '.join(allowed) or '无'}"
        )
    bound_roots: set[str] = set()
    for binding in node.input_bindings:
        target_root = binding.target_path.split(".", 1)[0]
        if target_root not in fields:
            allowed = _planning_field_names(fields)
            errors.append(
                f"节点 {node.node_id} 的 input_bindings 目标字段"
                f" {binding.target_path} 不存在；允许字段："
                f"{', '.join(allowed) or '无'}"
            )
            continue
        bound_roots.add(target_root)
    required = {
        name
        for name, field in fields.items()
        if field.is_required() and name not in RUNTIME_INPUT_FIELDS
    }
    missing = sorted(required - set(node.input_data) - bound_roots)
    if missing:
        errors.append(
            f"节点 {node.node_id} 的 input_data 缺少必填字段：{', '.join(missing)}"
        )
    for name, value in node.input_data.items():
        field = fields.get(name)
        if field is None or name in bound_roots:
            continue
        try:
            TypeAdapter(field.rebuild_annotation()).validate_python(value)
        except ValidationError as error:
            message = error.errors(include_url=False)[0].get("msg", "类型不匹配")
            errors.append(f"节点 {node.node_id} 的字段 {name}：{message}")
    return errors


def _validate_binding_contract(
    node: GeneralAgentPlanNode,
    source_path: str,
    target_path: str,
    source_node_id: str,
    source_contract: CapabilityContract,
    target_contract: CapabilityContract,
) -> list[str]:
    errors: list[str] = []
    target_schema = convert_to_openai_tool(target_contract.input_schema)["function"][
        "parameters"
    ]
    targets = _schemas_at_path(target_schema, target_path.split("."))
    if not targets:
        errors.append(
            f"节点 {node.node_id} 的 input_bindings 目标字段 {target_path} 不存在。"
        )
    if source_contract.output_schema is None:
        return errors
    source_schema = convert_to_openai_tool(source_contract.output_schema)["function"][
        "parameters"
    ]
    sources = _schemas_at_path(
        source_schema, source_path.removeprefix("output.").split(".")
    )
    if not sources:
        allowed = sorted(source_contract.output_schema.model_fields)
        errors.append(
            f"节点 {node.node_id} 的 input_bindings 来源路径 {source_path} 不存在于"
            f"上游节点 {source_node_id} 的输出；允许的顶层字段："
            f"{', '.join(allowed) or '无'}"
        )
    if errors:
        return errors
    if not any(
        _binding_types_overlap(source, target)
        for source in sources
        for target in targets
    ):
        return [
            f"节点 {node.node_id} 的 input_bindings 类型不兼容："
            f"{source_node_id}.{source_path} 不能绑定到 {target_path}；"
            "数组或对象不能代替文本，请绑定真实文本字段或改用上游产物上下文。"
        ]
    return []


def _schemas_at_path(schema: dict[str, Any], parts: list[str]) -> list[dict[str, Any]]:
    branches = schema.get("anyOf", [])
    if branches:
        return [
            resolved
            for branch in branches
            for resolved in _schemas_at_path(branch, parts)
        ]
    if not parts:
        return [schema]
    head, *tail = parts
    if head in schema.get("properties", {}):
        return _schemas_at_path(schema["properties"][head], tail)
    if head.isdigit() and isinstance(schema.get("items"), dict):
        return _schemas_at_path(schema["items"], tail)
    extra = schema.get("additionalProperties")
    if isinstance(extra, dict):
        return _schemas_at_path(extra, tail)
    if extra is True:
        return [{}]  # 动态字典只在执行时校验真实值，不能伪造确定类型。
    return []


def _binding_types_overlap(source: dict[str, Any], target: dict[str, Any]) -> bool:
    source_type, target_type = source.get("type"), target.get("type")
    if not source_type or not target_type:
        return True
    if source_type == "integer" and target_type == "number":
        return True
    if source_type != target_type:
        return False
    if source_type == "array":
        return _binding_types_overlap(source.get("items", {}), target.get("items", {}))
    return True


def _planning_field_names(fields: dict[str, Any]) -> list[str]:
    return sorted(name for name in fields if name not in RUNTIME_INPUT_FIELDS)


def _canonicalize_range_aliases(
    input_data: dict[str, Any],
    field_names: set[str],
) -> dict[str, Any]:
    normalized = dict(input_data)
    for boundary in ("start", "end"):
        if boundary not in normalized:
            continue
        matches = sorted(
            name for name in field_names if name.startswith(f"{boundary}_")
        )
        if len(matches) != 1 or matches[0] in normalized:
            continue
        normalized[matches[0]] = normalized.pop(boundary)

    for key in list(normalized):
        if not key.endswith("_range"):
            continue
        value = normalized[key]
        if not isinstance(value, dict) or not value:
            continue
        scope = key.removesuffix("_range")
        replacements: dict[str, Any] = {}
        valid = True
        for boundary, boundary_value in value.items():
            candidate = f"{boundary}_{scope}"
            if (
                boundary not in {"start", "end"}
                or candidate not in field_names
                or candidate in normalized
            ):
                valid = False
                break
            replacements[candidate] = boundary_value
        if not valid:
            continue
        normalized.pop(key)
        normalized.update(replacements)
    return normalized


def _native_tool_definition(contract: CapabilityContract) -> dict[str, Any]:
    async def schema_only_tool(**input_data: object) -> str:
        del input_data
        raise RuntimeError("规划阶段的能力 Schema 不可执行。")

    tool = StructuredTool.from_function(
        coroutine=schema_only_tool,
        name=contract.name,
        description=contract.description + _output_path_description(contract),
        args_schema=langchain_args_schema(contract.input_schema),
    )
    definition = convert_to_openai_tool(tool, strict=False)
    # 与执行前的未知字段校验保持一致，但不启用会强制补齐可选参数的严格模式。
    definition["function"]["parameters"]["additionalProperties"] = False
    return dict(definition)


def _output_path_description(contract: CapabilityContract) -> str:
    """从真实输出模型生成原生 Tool 的字段路径说明，不混入五层消息。"""

    if contract.output_schema is None:
        return ""
    schema = convert_to_openai_tool(contract.output_schema, strict=False)["function"][
        "parameters"
    ]
    paths = _schema_paths(schema)
    return (
        "\n本能力输出的可绑定字段路径（以输出根对象为准，数组下标 0 为示例）："
        + "；".join(paths)
        + "。artifact_type 的值只是产物类型，不是输出字段路径。"
    )


def _schema_paths(schema: dict[str, Any], prefix: str = "") -> list[str]:
    paths: list[str] = []
    for name, child in schema.get("properties", {}).items():
        path = f"{prefix}.{name}" if prefix else name
        paths.append(path)
        paths.extend(_schema_paths(child, path))
    items = schema.get("items")
    if isinstance(items, dict) and prefix:
        paths.append(f"{prefix}.0")
        paths.extend(_schema_paths(items, f"{prefix}.0"))
    for branch in schema.get("anyOf", []):
        paths.extend(_schema_paths(branch, prefix))
    return list(dict.fromkeys(paths))


def _search_text(item: dict[str, Any]) -> str:
    return json.dumps(item, ensure_ascii=False, separators=(",", ":")).casefold()


def _terms(value: str) -> set[str]:
    normalized = "".join(_WORD.findall(value.casefold()))
    chars = [char for char in normalized if "\u3400" <= char <= "\u9fff"]
    bigrams = {"".join(chars[index : index + 2]) for index in range(len(chars) - 1)}
    words = set(re.findall(r"[a-z0-9_]+", normalized))
    return words | bigrams | set(chars)


def _relevance(query_terms: set[str], document: str) -> float:
    if not query_terms:
        return 0.0
    score = 0.0
    for term in query_terms:
        if term not in document:
            continue
        score += 4.0 if len(term) > 1 else 0.25
    return score
