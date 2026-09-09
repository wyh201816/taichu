"""通用 Agent 业务审计投影与官方恢复边界。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from taichu.application.general_agent.models import (
    GeneralAgentContextEnvelope,
    GeneralAgentContextSnapshot,
    GeneralAgentCurrentRequest,
    GeneralAgentRun,
    GeneralAgentRunStatus,
    context_snapshot_sha256,
)
from taichu.infrastructure.general_agent_runs.json_repository import (
    JsonGeneralAgentRunRepository,
)

_CREATED_AT = "2026-08-30T00:00:00Z"


def _run(
    *,
    run_id: str,
    status: GeneralAgentRunStatus = GeneralAgentRunStatus.COMPLETED,
) -> GeneralAgentRun:
    snapshot_payload = {
        "snapshot_id": "context_20260830_000000_abcdef12",
        "phase": "plan",
        "conversation_id": "conversation_audit",
        "run_id": run_id,
        "created_at": _CREATED_AT,
        "policy_snapshot": {},
        "memory_refs": [
            {
                "memory_id": "memory_audit",
                "content_sha256": "a" * 64,
                "state_sha256": "a" * 64,
            }
        ],
        "envelope": GeneralAgentContextEnvelope(
            phase="plan",
            current_request=GeneralAgentCurrentRequest(content="继续审计。"),
        ).model_dump(mode="json"),
        "assembly_trace": None,
    }
    snapshot = GeneralAgentContextSnapshot.model_validate(
        {
            **snapshot_payload,
            "content_sha256": context_snapshot_sha256(snapshot_payload),
        }
    )
    return GeneralAgentRun(
        run_id=run_id,
        task_id="conversation_audit",
        conversation_id="conversation_audit",
        request_index=1,
        user_goal="继续审计。",
        status=status,
        context_snapshot_id=snapshot.snapshot_id,
        context_snapshot=snapshot,
        created_at=_CREATED_AT,
        updated_at=_CREATED_AT,
        started_at=_CREATED_AT,
        finished_at=(
            _CREATED_AT if status is GeneralAgentRunStatus.COMPLETED else None
        ),
    )


def test_legacy_snapshot_validation_failure_keeps_run_audit_only(
    tmp_path: Path,
) -> None:
    repository = JsonGeneralAgentRunRepository(tmp_path)
    run = _run(run_id="general_run_20260830_000000_abc123")
    asyncio.run(repository.save(run))
    path = (
        tmp_path / "derived" / "general_agent_runs" / f"{run.run_id}.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["context_snapshot"]["memory_refs"][0]["state_sha256"]
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    loaded = asyncio.run(repository.get(run.run_id))

    assert loaded is not None
    assert loaded.context_snapshot_id is None
    assert loaded.context_snapshot is None
    assert loaded.status is GeneralAgentRunStatus.COMPLETED
    assert any(
        "图恢复仅使用 LangGraph Checkpointer" in item
        for item in loaded.context_resume_differences
    )


def test_status_query_does_not_parse_unrelated_corrupt_history(
    tmp_path: Path,
) -> None:
    repository = JsonGeneralAgentRunRepository(tmp_path)
    active = _run(
        run_id="general_run_20260830_000001_def456",
        status=GeneralAgentRunStatus.EXECUTING,
    )
    asyncio.run(repository.save(active))
    corrupt_path = (
        tmp_path
        / "derived"
        / "general_agent_runs"
        / "general_run_20260830_000002_ghi789.json"
    )
    corrupt_path.write_text(
        json.dumps(
            {
                "run_id": "general_run_20260830_000002_ghi789",
                "status": "completed",
                "user_goal": "",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    runs, total = asyncio.run(
        repository.list_runs(status=GeneralAgentRunStatus.EXECUTING.value)
    )

    assert total == 1
    assert [item.run_id for item in runs] == [active.run_id]
