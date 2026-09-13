"""把专业子 Agent 的逻辑模型角色映射到具体模型。"""

import json
from typing import Mapping


class ModelRoleRouter:
    """保持 Agent 业务代码与具体模型 ID 解耦。"""

    def __init__(
        self,
        default_model_id: str,
        overrides: Mapping[str, str] | None = None,
    ) -> None:
        if not default_model_id.strip():
            raise ValueError("默认模型 ID 不能为空。")
        self._default_model_id = default_model_id
        self._overrides = {
            role: model_id
            for role, model_id in (overrides or {}).items()
            if role.strip() and model_id.strip()
        }

    @classmethod
    def from_json(
        cls,
        default_model_id: str,
        mapping_json: str,
    ) -> "ModelRoleRouter":
        try:
            raw = json.loads(mapping_json or "{}")
        except json.JSONDecodeError as error:
            raise ValueError("专业子 Agent 模型角色配置不是合法 JSON。") from error
        if not isinstance(raw, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in raw.items()
        ):
            raise ValueError("专业子 Agent 模型角色配置必须是字符串映射。")
        return cls(default_model_id, raw)

    def model_for(self, role: str) -> str:
        return self._overrides.get(
            role,
            "deepseek-v4-flash"
            if role == "context_extractor"
            else self._default_model_id,
        )

    def configured_roles(self) -> dict[str, str]:
        return dict(self._overrides)
