"""LangChain RunnableConfig 中的太初模型调用元数据。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig


TAICHU_MODEL_REQUEST_METADATA_KEY = "taichu_model_request"


def model_call_config(
    *,
    model_id: str,
    task_type: str,
    task_name: str,
    run_id: str | None = None,
    context_snapshot_id: str | None = None,
    chapter_ids: Sequence[str] = (),
    feature: str = "",
    temperature: float | None = None,
    max_output_tokens: int | None = None,
    callbacks: Sequence[Any] = (),
) -> RunnableConfig:
    """用官方 Runnable 元数据承载供应商无关的请求级设置。"""

    request_metadata: dict[str, Any] = {
        "model_id": model_id,
        "task_type": task_type,
        "task_name": task_name,
        "run_id": run_id,
        "context_snapshot_id": context_snapshot_id,
        "chapter_ids": list(chapter_ids),
        "feature": feature,
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
    }
    config: RunnableConfig = {
        "metadata": {TAICHU_MODEL_REQUEST_METADATA_KEY: request_metadata},
        "tags": ["taichu-model-call", task_type],
    }
    if callbacks:
        config["callbacks"] = list(callbacks)
    return config
