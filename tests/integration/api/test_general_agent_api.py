"""通用写作助手 Runtime HTTP 全链路测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path

from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from taichu.application.contracts.llm import LLMModelProfile
from taichu.infrastructure.llm.contracts import (
    LLMCost,
    LLMRequest,
    LLMResponse,
    LLMStreamEvent,
    LLMToolCall,
    LLMUsage,
)
from taichu.config import Settings
from taichu.application.models.llm_replay import (
    LLMCallReplayRecord,
    LLMReplayMessage,
)
from taichu.main import create_app
from tests.fakes import (
    InMemoryGeneralAgentToolBudgetRepository,
    InMemoryKnowledgeRepository,
)


class _DirectAnswerGateway:
    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []
        self.fail_next_orchestration = False
        self.replan_first_verification = False
        self.verification_count = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if self.fail_next_orchestration and request.task_name in {
            "general_writing_orchestrator.plan",
            "general_writing_orchestrator.replan",
        }:
            self.fail_next_orchestration = False
            raise RuntimeError("一次性模型错误")
        payload: dict[str, object]
        if request.task_name == "context.extract":
            fragment = json.loads(
                next(
                    message.content
                    for message in request.messages
                    if message.role == "developer"
                )
            )
            sources = fragment["最近对话及新增结果"]
            user_source = next(
                key for key, value in sources.items() if value.get("角色") == "user"
            )
            payload = {
                "task_goal": {
                    "goal": {"content": "规划场景冲突。", "source_ids": [user_source]}
                }
            }
            constraint_source = next(
                (key for key, value in sources.items() if "作者约束" in value), None
            )
            if constraint_source:
                payload["constraints"] = {
                    "name": {
                        "content": "不要改变秦阳的姓名。",
                        "source_ids": [constraint_source],
                    }
                }
        elif request.task_name.startswith(
            ("general_writing_orchestrator.plan", "general_writing_orchestrator.replan")
        ):
            if self.replan_first_verification:
                payload = {
                    "rationale": "先读取当前小说结构，再规划需要重检的冲突场景。",
                    "nodes": [
                        {
                            "node_id": "read_structure",
                            "kind": "tool",
                            "capability_name": "get_novel_structure",
                            "objective": "读取当前小说结构。",
                        }
                    ],
                }
            else:
                payload = {
                    "rationale": "这是不依赖小说事实的通用写作方法问题，可以直接回答。",
                    "direct_response": "可以先明确场景目标，再安排冲突和信息释放。",
                    "nodes": [],
                }
        else:
            self.verification_count += 1
            if self.replan_first_verification and self.verification_count == 1:
                payload = {
                    "outcome": "partial",
                    "final_answer": "第一次校验要求重规划。",
                    "issues": ["需要补充冲突升级方式。"],
                    "should_replan": True,
                    "replan_guidance": "补充冲突升级方式后重新回答。",
                }
            else:
                payload = {
                    "outcome": "satisfied",
                    "final_answer": "可以先明确场景目标，再安排冲突升级与信息释放。",
                    "issues": [],
                    "should_replan": False,
                }
        tool_calls = (
            (
                LLMToolCall(
                    call_id=f"call_{len(self.requests)}_{request.tool_choice}",
                    name=request.tool_choice,
                    arguments_json=json.dumps(payload, ensure_ascii=False),
                ),
            )
            if request.tools
            else ()
        )
        return LLMResponse(
            text="" if tool_calls else json.dumps(payload, ensure_ascii=False),
            model_id=request.model_id,
            upstream_model=request.model_id,
            usage=LLMUsage(input_tokens=20, output_tokens=20, total_tokens=40),
            cost=LLMCost(amount=Decimal("0.001"), kind="estimated"),
            tool_calls=tool_calls,
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamEvent]:
        del request
        if False:
            yield LLMStreamEvent(event_type="completed")

    def list_models(self) -> list[LLMModelProfile]:
        return []


class GeneralAgentApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.assets_root = Path(self._temporary_directory.name)
        self.gateway = _DirectAnswerGateway()
        self.app = create_app(
            app_settings=Settings(project_assets_dir=self.assets_root),
            llm_gateway=self.gateway,
            knowledge_repository=InMemoryKnowledgeRepository(),
            graph_checkpointer=InMemorySaver(),
            graph_store=InMemoryStore(),
            tool_budget_repository=InMemoryGeneralAgentToolBudgetRepository(),
        )
        self.client = AsyncClient(
            transport=ASGITransport(app=self.app),
            base_url="http://test",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self._temporary_directory.cleanup()

    async def test_run_list_detail_and_delete(self) -> None:
        response = await self.client.post(
            "/api/agent-workbench/general-assistant/runs",
            json={
                "user_goal": "怎样规划一个有冲突的场景？",
                "model_id": "deepseek-v4-pro",
                "start_new_conversation": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        run = response.json()["run"]
        self.assertEqual(run["status"], "completed")
        self.assertIn("信息释放", run["final_answer"])
        self.assertEqual(run["agent_name"], "general_writing_assistant")
        self.assertEqual(run["model_id"], "deepseek-v4-pro")
        self.assertTrue(self.gateway.requests)
        self.assertTrue(
            all(
                request.model_id
                == (
                    "deepseek-v4-flash"
                    if request.task_name == "context.extract"
                    else "deepseek-v4-pro"
                )
                for request in self.gateway.requests
            )
        )
        run_id = run["run_id"]

        detail = await self.client.get(
            f"/api/agent-workbench/general-assistant/runs/{run_id}"
        )
        listing = await self.client.get("/api/agent-workbench/general-assistant/runs")
        traces = await self.client.get(
            f"/api/agent-workbench/general-assistant/runs/{run_id}/traces"
        )
        recovery = await self.client.get(
            f"/api/agent-workbench/general-assistant/runs/{run_id}/recovery"
        )
        replay_record = LLMCallReplayRecord(
            call_id="llm-call-" + "a" * 32,
            run_id=run_id,
            task_type="general_agent",
            task_name="general_writing_orchestrator.plan",
            model_id="test-model",
            upstream_model="test-model",
            wire_protocol="openai_responses",
            status="completed",
            messages=[LLMReplayMessage(role="user", content="测试请求")],
            response_text='{"result":"测试响应"}',
            request_sha256="1" * 64,
            response_sha256="2" * 64,
            started_at=run["created_at"],
            finished_at=run["updated_at"],
            duration_ms=1,
        )
        await self.app.state.llm_replay_repository.save(replay_record)
        context_snapshots = await self.client.get(
            f"/api/agent-workbench/general-assistant/runs/{run_id}/context-snapshots"
        )
        llm_replays = await self.client.get(
            f"/api/agent-workbench/general-assistant/runs/{run_id}/llm-replays"
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(traces.status_code, 200)
        self.assertEqual(recovery.status_code, 200)
        self.assertEqual(context_snapshots.status_code, 200)
        self.assertEqual(llm_replays.status_code, 200)
        self.assertEqual(listing.json()["total"], 1)
        self.assertEqual(listing.json()["runs"][0]["run_id"], run_id)
        self.assertEqual(
            listing.json()["runs"][0]["conversation_id"],
            run["conversation_id"],
        )
        self.assertIsNotNone(listing.json()["runs"][0]["context_snapshot_id"])
        self.assertEqual(traces.json()["total"], 2)
        self.assertEqual(
            [item["capability_name"] for item in traces.json()["traces"]],
            [
                "general_writing_orchestrator.plan",
                "context.extract",
            ],
        )
        self.assertTrue(
            all(item["run_id"] == run_id for item in traces.json()["traces"])
        )
        self.assertEqual(
            recovery.json()["recovery"]["checkpoint"]["status"],
            "available",
        )
        self.assertGreater(
            recovery.json()["recovery"]["checkpoint"]["checkpoint_count"],
            0,
        )
        self.assertGreater(len(recovery.json()["recovery"]["checkpoints"]), 0)
        self.assertEqual(
            recovery.json()["recovery"]["checkpoints"][0]["checkpoint_id"],
            recovery.json()["recovery"]["checkpoint"]["latest_checkpoint_id"],
        )
        self.assertIn(
            recovery.json()["recovery"]["checkpoints"][0]["source"],
            {"input", "loop", "update", "fork"},
        )
        self.assertEqual(recovery.json()["recovery"]["effects"], [])
        self.assertEqual(context_snapshots.json()["total"], 1)
        self.assertEqual(
            [item["phase"] for item in context_snapshots.json()["snapshots"]],
            ["plan"],
        )
        self.assertEqual(llm_replays.json()["total"], 1)
        self.assertEqual(
            llm_replays.json()["calls"][0]["response_text"],
            '{"result":"测试响应"}',
        )

        deleted = await self.client.delete(
            f"/api/agent-workbench/general-assistant/runs/{run_id}"
        )
        missing = await self.client.get(
            f"/api/agent-workbench/general-assistant/runs/{run_id}"
        )
        missing_traces = await self.client.get(
            f"/api/agent-workbench/general-assistant/runs/{run_id}/traces"
        )
        missing_recovery = await self.client.get(
            f"/api/agent-workbench/general-assistant/runs/{run_id}/recovery"
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing_traces.status_code, 404)
        self.assertEqual(missing_recovery.status_code, 404)
        self.assertFalse(
            (
                self.assets_root
                / "derived"
                / "general_agent_context_snapshots"
                / run_id
            ).exists()
        )
        self.assertFalse(
            (
                self.assets_root
                / "derived"
                / "llm_call_replays"
                / f"{replay_record.call_id}.json"
            ).exists()
        )
        self.assertEqual(
            [request.task_name for request in self.gateway.requests],
            [
                "general_writing_orchestrator.plan",
                "context.extract",
            ],
        )

    async def test_replan_records_each_context_phase_in_order(self) -> None:
        self.gateway.replan_first_verification = True
        response = await self.client.post(
            "/api/agent-workbench/general-assistant/runs",
            json={
                "user_goal": "规划一个需要重检的冲突场景。",
                "start_new_conversation": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        run = response.json()["run"]
        self.assertEqual(run["status"], "completed", run.get("errors"))
        self.assertEqual(run["replan_count"], 1)

        snapshots = await self.client.get(
            "/api/agent-workbench/general-assistant/runs/"
            f"{run['run_id']}/context-snapshots"
        )
        self.assertEqual(snapshots.status_code, 200)
        payload = snapshots.json()
        expected_phases = [
            request.task_name.removeprefix("general_writing_orchestrator.")
            for request in self.gateway.requests
            if request.task_name.startswith("general_writing_orchestrator.")
        ]
        self.assertEqual(payload["total"], len(expected_phases))
        self.assertEqual(
            [item["phase"] for item in payload["snapshots"]],
            [phase.split(".")[0] for phase in expected_phases],
        )
        self.assertEqual(
            [phase for phase in expected_phases if "." not in phase],
            ["plan", "verify", "replan", "verify"],
        )
        self.assertEqual(
            len({item["snapshot_id"] for item in payload["snapshots"]}),
            len(expected_phases),
        )

    async def test_failed_run_resume_clears_recovered_error(self) -> None:
        self.gateway.fail_next_orchestration = True
        failed_response = await self.client.post(
            "/api/agent-workbench/general-assistant/runs",
            json={
                "user_goal": "这次调用会先遇到一次瞬时错误。",
                "start_new_conversation": True,
            },
        )
        self.assertEqual(failed_response.status_code, 200)
        failed = failed_response.json()["run"]
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["errors"], ["一次性模型错误"])

        resumed_response = await self.client.post(
            f"/api/agent-workbench/general-assistant/runs/{failed['run_id']}/resume",
            json={},
        )
        self.assertEqual(resumed_response.status_code, 200)
        resumed = resumed_response.json()["run"]
        self.assertEqual(resumed["run_id"], failed["run_id"])
        self.assertIsNone(resumed["parent_run_id"])
        self.assertEqual(resumed["conversation_id"], failed["conversation_id"])
        self.assertEqual(resumed["request_index"], 1)
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(resumed["errors"], [])

        preserved_response = await self.client.get(
            f"/api/agent-workbench/general-assistant/runs/{failed['run_id']}"
        )
        self.assertEqual(preserved_response.status_code, 200)
        self.assertEqual(preserved_response.json()["run"]["status"], "completed")
        self.assertEqual(preserved_response.json()["run"]["errors"], [])

    async def test_conversation_keeps_multiple_requests_and_can_be_deleted(
        self,
    ) -> None:
        first_response = await self.client.post(
            "/api/agent-workbench/general-assistant/runs",
            json={
                "user_goal": "第一次请求：怎样安排场景冲突？",
                "start_new_conversation": True,
            },
        )
        self.assertEqual(first_response.status_code, 200)
        first_run = first_response.json()["run"]
        conversation_id = first_run["conversation_id"]
        self.assertTrue(conversation_id.startswith("general_conversation_"))
        self.assertIsNone(first_run["parent_run_id"])
        self.assertEqual(first_run["request_index"], 1)

        second_response = await self.client.post(
            "/api/agent-workbench/general-assistant/runs",
            json={
                "user_goal": "第二次请求：把冲突升级得更自然一些。",
                "conversation_id": conversation_id,
                "start_new_conversation": False,
            },
        )
        self.assertEqual(second_response.status_code, 200)
        second_run = second_response.json()["run"]
        self.assertEqual(second_run["conversation_id"], conversation_id)
        self.assertEqual(second_run["parent_run_id"], first_run["run_id"])
        self.assertEqual(second_run["request_index"], 2)
        self.assertEqual(
            [message["role"] for message in second_run["messages"]],
            ["user", "assistant", "user", "assistant"],
        )
        self.assertEqual(
            second_run["messages"][0]["content"],
            "第一次请求：怎样安排场景冲突？",
        )
        self.assertEqual(
            second_run["messages"][-2]["content"],
            "第二次请求：把冲突升级得更自然一些。",
        )
        self.assertIn("信息释放", second_run["messages"][1]["content"])
        self.assertIn("信息释放", second_run["messages"][-1]["content"])

        listing = await self.client.get(
            "/api/agent-workbench/general-assistant/conversations"
        )
        detail = await self.client.get(
            f"/api/agent-workbench/general-assistant/conversations/{conversation_id}"
        )
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["total"], 1)
        self.assertEqual(listing.json()["conversations"][0]["request_count"], 2)
        self.assertEqual(
            listing.json()["conversations"][0]["conversation_id"],
            conversation_id,
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(
            [run["run_id"] for run in detail.json()["runs"]],
            [first_run["run_id"], second_run["run_id"]],
        )

        deleted = await self.client.delete(
            f"/api/agent-workbench/general-assistant/conversations/{conversation_id}"
        )
        empty_listing = await self.client.get(
            "/api/agent-workbench/general-assistant/conversations"
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["deleted_count"], 2)
        self.assertEqual(empty_listing.json()["total"], 0)

    async def test_runtime_memories_are_automatic_and_read_only(
        self,
    ) -> None:
        response = await self.client.post(
            "/api/agent-workbench/general-assistant/runs",
            json={
                "user_goal": "给出一套场景冲突规划方法。",
                "author_constraints": ["不要改变秦阳的姓名。"],
                "start_new_conversation": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        run = response.json()["run"]
        conversation_id = run["conversation_id"]

        listing = await self.client.get(
            "/api/agent-workbench/general-assistant/"
            f"conversations/{conversation_id}/memories"
        )
        self.assertEqual(listing.status_code, 200)
        # 该入口只保留旧记录及执行有效性审计；不再双写语义记忆。
        self.assertEqual(listing.json()["memories"], [])
        memory = run["context_pipeline"]["working_memory"]
        self.assertEqual(
            set(memory),
            {
                "task_goal",
                "file_list",
                "workflow_state",
                "error_knowledge",
                "constraints",
            },
        )
        self.assertEqual(
            memory["constraints"]["name"]["content"], "不要改变秦阳的姓名。"
        )
        self.assertEqual(
            memory["constraints"]["name"]["source_ids"],
            [f"request:{run['run_id']}:constraints"],
        )
        self.assertTrue(memory["task_goal"])
        self.assertNotIn("lifecycle", json.dumps(memory))

        rejected_delete = await self.client.delete(
            "/api/agent-workbench/general-assistant/memories/name"
        )
        self.assertEqual(rejected_delete.status_code, 405)
        rejected_create = await self.client.post(
            "/api/agent-workbench/general-assistant/memories",
            json={"kind": "task_summary", "content": "用户手工写入"},
        )
        self.assertEqual(rejected_create.status_code, 404)
        visible = await self.client.get(
            f"/api/agent-workbench/general-assistant/runs/{run['run_id']}"
        )
        self.assertEqual(
            visible.json()["run"]["context_pipeline"]["working_memory"], memory
        )
