"""完整结果的不可变磁盘副本；图恢复仍由官方 Checkpoint/Store 负责。"""

import asyncio
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any
from uuid import uuid4


class JsonContextResultStore:
    def __init__(self, project_assets_dir: Path) -> None:
        self._root = project_assets_dir / "derived" / "general_agent_results"

    async def save(
        self, conversation_id: str, source_id: str, output: dict[str, Any]
    ) -> str:
        payload = {
            "conversation_id": conversation_id,
            "source_id": source_id,
            "output": output,
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        reference = "result_" + sha256(encoded.encode()).hexdigest()
        await asyncio.to_thread(self._write, reference, encoded)
        return reference

    def _write(self, reference: str, encoded: str) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._path(reference)
        if path.exists():
            if path.read_text(encoding="utf-8") != encoded:
                raise ValueError("完整结果文件校验失败，不能覆盖已有记录。")
            return
        temporary = path.with_name(f".{reference}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(encoded, encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    async def read(self, conversation_id: str, result_ref: str) -> dict[str, Any]:
        encoded = await asyncio.to_thread(
            self._path(result_ref).read_text, encoding="utf-8"
        )
        if "result_" + sha256(encoded.encode()).hexdigest() != result_ref:
            raise ValueError("完整结果内容哈希不匹配。")
        payload = json.loads(encoded)
        if payload["conversation_id"] != conversation_id:
            raise ValueError("完整结果不属于当前会话。")
        return dict(payload["output"])

    def _path(self, reference: str) -> Path:
        if re.fullmatch(r"result_[a-f0-9]{64}", reference) is None:
            raise ValueError("完整结果引用无效。")
        return self._root / f"{reference}.json"
