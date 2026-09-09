"""锁定 Agent / LLM 官方协议与供应商适配的分层边界。"""

from __future__ import annotations

import ast
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src" / "taichu"
_BUSINESS_ROOTS = (_SRC / "application", _SRC / "domain")
_PROVIDER_LLM_MODULE = "taichu.infrastructure.llm"
_FORBIDDEN_TRANSPORT_SYMBOLS = {
    "LLMGatewayContract",
    "LLMRequest",
    "LLMResponse",
    "LLMStreamEvent",
    "LLMToolCall",
    "LLMToolDefinition",
}
_REMOVED_RUNTIME_SYMBOLS = {
    "deepagents",
    "LangChainLLMAdapter",
    "MVPNoRealLLMChatModel",
}
_MODEL_CALL_SIGNALS = {
    "BaseChatModel",
    "create_agent",
    ".bind_tools",
    ".with_structured_output",
}
_MODEL_JSON_PARSERS = {"extract_json", "parse_json", "repair_json"}
_PSEUDO_AGENT_KEYS = ('"tool":', '"arguments":', '"action":', '"thought":')


def test_application_and_domain_do_not_import_provider_llm() -> None:
    violations: list[str] = []
    for path in _business_python_files():
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if _is_provider_llm_module(module):
                    violations.append(_location(path, node, module))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_provider_llm_module(alias.name):
                        violations.append(_location(path, node, alias.name))

    assert not violations, "业务层依赖了供应商 LLM 适配层：\n" + "\n".join(violations)


def test_removed_agent_runtime_layers_do_not_return() -> None:
    removed_files = (
        _SRC / "application" / "services" / "llm_tool_loop.py",
        _SRC / "infrastructure" / "llm" / "mock.py",
    )
    present = [str(path.relative_to(_ROOT)) for path in removed_files if path.exists()]
    assert not present, "已删除的 Agent Runtime 层重新出现：\n" + "\n".join(present)

    violations: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for symbol in _REMOVED_RUNTIME_SYMBOLS:
            if symbol.lower() in source.lower():
                violations.append(f"{path.relative_to(_ROOT)}: {symbol}")

    for manifest_path in (_ROOT / "pyproject.toml", _ROOT / "uv.lock"):
        source = manifest_path.read_text(encoding="utf-8").lower()
        if "deepagents" in source:
            violations.append(f"{manifest_path.relative_to(_ROOT)}: deepagents")

    assert not violations, "旧 Agent Runtime 符号或依赖重新出现：\n" + "\n".join(
        violations
    )


def test_application_does_not_recreate_llm_transport_dtos() -> None:
    violations: list[str] = []
    for path in _business_python_files():
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in _FORBIDDEN_TRANSPORT_SYMBOLS:
                violations.append(_location(path, node, node.name))
            elif isinstance(node, ast.ImportFrom | ast.Import):
                for name in _imported_names(node):
                    if name in _FORBIDDEN_TRANSPORT_SYMBOLS:
                        violations.append(_location(path, node, name))

    assert not violations, "业务层重新定义或导入了 LLM transport DTO：\n" + "\n".join(
        violations
    )


def test_model_contracts_are_not_handwritten_in_prompts() -> None:
    violations: list[str] = []
    for path in _business_python_files():
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        docstring_ids = _docstring_node_ids(tree)

        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in docstring_ids:
                    continue
                lowered = node.value.lower()
                pseudo_keys = sum(key in lowered for key in _PSEUDO_AGENT_KEYS)
                if (
                    "```json" in lowered
                    or "只返回 json" in lowered
                    or "仅输出 json" in lowered
                    or pseudo_keys >= 2
                ):
                    violations.append(_location(path, node, "手写模型 JSON/Tool 协议"))
            elif isinstance(node, ast.Dict) and _is_manual_function_schema(node):
                violations.append(_location(path, node, "手写 function schema"))

        if any(signal in source for signal in _MODEL_CALL_SIGNALS):
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and _is_model_json_parser(node.func):
                    violations.append(_location(path, node, "手工解析模型 JSON"))

    assert not violations, "发现应由原生 Tool/structured output 承担的协议：\n" + "\n".join(
        violations
    )


def _business_python_files() -> list[Path]:
    return sorted(path for root in _BUSINESS_ROOTS for path in root.rglob("*.py"))


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _is_provider_llm_module(module: str) -> bool:
    return module == _PROVIDER_LLM_MODULE or module.startswith(
        f"{_PROVIDER_LLM_MODULE}."
    )


def _imported_names(node: ast.ImportFrom | ast.Import) -> tuple[str, ...]:
    return tuple(alias.name.rsplit(".", 1)[-1] for alias in node.names)


def _docstring_node_ids(tree: ast.AST) -> set[int]:
    result: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if not node.body:
            continue
        first = node.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            result.add(id(first.value))
    return result


def _is_manual_function_schema(node: ast.Dict) -> bool:
    pairs = {
        key.value: value.value
        for key, value in zip(node.keys, node.values, strict=True)
        if isinstance(key, ast.Constant)
        and isinstance(key.value, str)
        and isinstance(value, ast.Constant)
    }
    return pairs.get("type") == "function" and "function" in {
        key.value for key in node.keys if isinstance(key, ast.Constant)
    }


def _is_model_json_parser(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id in _MODEL_JSON_PARSERS
    return (
        isinstance(node, ast.Attribute)
        and (
            node.attr in _MODEL_JSON_PARSERS
            or (
                node.attr == "loads"
                and isinstance(node.value, ast.Name)
                and node.value.id == "json"
            )
        )
    )


def _location(path: Path, node: ast.AST, detail: str) -> str:
    return f"{path.relative_to(_ROOT)}:{getattr(node, 'lineno', 1)} {detail}"
