"""经真实助手入口验证轨迹采集和只读查询。"""

from __future__ import annotations

import tempfile
import unittest
import json
import asyncio
from dataclasses import replace
from pathlib import Path

from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from taichu.config import Settings
from taichu.main import create_app
from tests.fakes import (
    InMemoryGeneralAgentToolBudgetRepository,
    InMemoryKnowledgeRepository,
)
from tests.integration.api.test_general_agent_api import _DirectAnswerGateway


class _ParallelToolsGateway(_DirectAnswerGateway):
    async def complete(self, request):
        response = await super().complete(request)
        if request.task_name.startswith("general_writing_orchestrator.plan"):
            payload = {"rationale": "合成并行读取", "nodes": [
                {"node_id": node_id, "kind": "tool", "capability_name": "get_novel_structure", "objective": "读取小说结构"}
                for node_id in ("left", "right")
            ]}
            call = replace(response.tool_calls[0], call_id="重复的原生局部标识", arguments_json=json.dumps(payload, ensure_ascii=False))
            return replace(response, tool_calls=(call,))
        return response


class GeneralAgentTrajectoryApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.assets = Path(self.temporary.name)
        self.gateway = _DirectAnswerGateway()
        self.app = create_app(
            app_settings=Settings(project_assets_dir=self.assets),
            llm_gateway=self.gateway,
            knowledge_repository=InMemoryKnowledgeRepository(),
            graph_checkpointer=InMemorySaver(),
            graph_store=InMemoryStore(),
            tool_budget_repository=InMemoryGeneralAgentToolBudgetRepository(),
        )
        self.client = AsyncClient(
            transport=ASGITransport(app=self.app), base_url="http://test"
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.temporary.cleanup()

    async def test_new_request_exposes_actual_user_and_model_records(self) -> None:
        goal = "怎样规划冲突？\n保留原文与标点。"
        response = await self.client.post(
            "/api/agent-workbench/general-assistant/runs",
            json={"user_goal": goal, "start_new_conversation": True},
        )
        self.assertEqual(response.status_code, 200)
        run = response.json()["run"]
        calls_before_read = len(self.gateway.requests)
        trace = await self.client.get(
            "/api/agent-workbench/general-assistant/conversations/"
            f"{run['conversation_id']}/trajectory"
        )
        self.assertEqual(trace.status_code, 200)
        payload = trace.json()
        self.assertTrue(payload["complete"])
        events = payload["events"]
        self.assertEqual(events[0]["kind"], "user")
        self.assertEqual(events[0]["data"]["content"], goal)
        models = [event for event in events if event["kind"] == "model_completed"]
        self.assertEqual(len(models), calls_before_read)
        inputs = [event for event in events if event["kind"] == "model_started"]
        self.assertEqual(len(inputs), calls_before_read)
        self.assertEqual(
            inputs[0]["data"]["request"]["messages"][-1]["content"],
            self.gateway.requests[0].messages[-1].content,
        )
        self.assertEqual(len(self.gateway.requests), calls_before_read)
        reloaded = await self.client.get(
            "/api/agent-workbench/general-assistant/conversations/"
            f"{run['conversation_id']}/trajectory"
        )
        self.assertEqual(reloaded.json()["events"], events)

    async def test_real_tools_pair_results_and_preserve_model_origin(self) -> None:
        self.gateway.replan_first_verification = True
        response = await self.client.post(
            "/api/agent-workbench/general-assistant/runs",
            json={"user_goal": "读取小说结构，再规划冲突。", "start_new_conversation": True},
        )
        run = response.json()["run"]
        self.assertEqual(run["status"], "completed")
        trace = await self.client.get(
            "/api/agent-workbench/general-assistant/conversations/"
            f"{run['conversation_id']}/trajectory"
        )
        events = trace.json()["events"]
        starts = [event for event in events if event["kind"] == "tool_started"]
        self.assertEqual(len(starts), 2)
        completions = {event["call_id"]: event for event in events if event["kind"] == "tool_completed"}
        models = {event["call_id"] for event in events if event["kind"] == "model_started"}
        self.assertEqual(len(completions), 2)
        for start in starts:
            self.assertEqual(start["data"]["name"], "get_novel_structure")
            self.assertIn(start["parent_call_id"], models)
            self.assertIn("result", completions[start["call_id"]]["data"])
            self.assertIn("properties", start["data"]["schema"])
        # 模型结构化输出协议不会成为额外工具活动。
        self.assertTrue(all(event["data"]["name"] == "get_novel_structure" for event in starts))
        page = (await self.client.get(
            "/api/agent-workbench/general-assistant/conversations/"
            f"{run['conversation_id']}/trajectory/records"
        )).json()
        assistants = [row for row in page["records"] if row["type"] == "assistant"]
        self.assertEqual(len(assistants), len(self.gateway.requests))
        self.assertEqual([row["step"] for row in assistants], list(range(1, len(assistants) + 1)))
        self.assertEqual([row["requestNumber"] for row in assistants], list(range(1, len(assistants) + 1)))
        by_id = {row["id"]: row for row in assistants}
        for tool in [row for row in page["records"] if row["type"] == "tool"]:
            self.assertEqual(tool["step"], by_id[tool["parent_id"]]["step"])
            self.assertIsNone(tool["requestNumber"])
            base = f"/api/agent-workbench/general-assistant/conversations/{run['conversation_id']}/trajectory"
            detail = (await self.client.get(base + "/records/" + tool["parent_id"])).json()
            tool_start = next(start for start in starts if start["event_id"] == tool["id"])
            self.assertIn({"id": tool["id"], "native_call_id": tool_start["data"]["native_call_id"]}, detail["tool_records"])

    async def test_disabling_observation_preserves_execution_and_creates_no_records(self) -> None:
        disabled_gateway = _DirectAnswerGateway()
        disabled = create_app(
            app_settings=Settings(project_assets_dir=self.assets / "disabled", general_agent_trajectory_enabled=False),
            llm_gateway=disabled_gateway,
            knowledge_repository=InMemoryKnowledgeRepository(),
            graph_checkpointer=InMemorySaver(), graph_store=InMemoryStore(),
            tool_budget_repository=InMemoryGeneralAgentToolBudgetRepository(),
        )
        request = {"user_goal": "怎样安排信息释放？", "start_new_conversation": True}
        enabled_result = await self.client.post("/api/agent-workbench/general-assistant/runs", json=request)
        async with AsyncClient(transport=ASGITransport(app=disabled), base_url="http://test") as client:
            disabled_result = await client.post("/api/agent-workbench/general-assistant/runs", json=request)
            run = disabled_result.json()["run"]
            trace = await client.get(f"/api/agent-workbench/general-assistant/conversations/{run['conversation_id']}/trajectory")
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["final_answer"], enabled_result.json()["run"]["final_answer"])
        self.assertEqual(len(disabled_gateway.requests), len(self.gateway.requests))
        self.assertEqual(trace.json()["events"], [])

    async def test_history_search_details_and_incremental_reads_are_isolated_and_read_only(self) -> None:
        first = await self.client.post("/api/agent-workbench/general-assistant/runs", json={
            "user_goal": "早期独有关键词：雾桥回声", "start_new_conversation": True,
        })
        conversation_id = first.json()["run"]["conversation_id"]
        await self.client.post("/api/agent-workbench/general-assistant/runs", json={
            "user_goal": "后来请求", "conversation_id": conversation_id,
        })
        calls = len(self.gateway.requests)
        base = f"/api/agent-workbench/general-assistant/conversations/{conversation_id}/trajectory"
        latest = await self.client.get(base + "/records", params={"limit": 2})
        self.assertEqual(latest.status_code, 200)
        page = latest.json()
        self.assertEqual(len(page["records"]), 2)
        self.assertTrue(page["has_earlier"])
        self.assertTrue(all("messages" not in str(row["start"]["data"]) for row in page["records"]))
        earlier = await self.client.get(base + "/records", params={"before": page["before"], "limit": 2})
        self.assertEqual(page["turns"], earlier.json()["turns"])
        search = await self.client.get(base + "/search", params={"q": "雾桥回声"})
        hits = search.json()["hits"]
        self.assertTrue(hits)
        located = await self.client.get(base + "/records", params={"around": hits[0]["record_id"], "limit": 2})
        self.assertIn(hits[0]["record_id"], [row["id"] for row in located.json()["records"]])
        detail = await self.client.get(base + "/records/" + hits[0]["record_id"])
        self.assertEqual(detail.status_code, 200)
        self.assertIn("雾桥回声", str(detail.json()))
        unchanged = await self.client.get(base + "/records", params={"after": page["cursor"]})
        self.assertEqual(unchanged.json()["records"], [])
        other = await self.client.post("/api/agent-workbench/general-assistant/runs", json={
            "user_goal": "别的会话", "start_new_conversation": True,
        })
        other_id = other.json()["run"]["conversation_id"]
        self.assertEqual((await self.client.get(base.replace(conversation_id, other_id) + "/records/" + hits[0]["record_id"])).status_code, 404)
        self.assertEqual(len(self.gateway.requests), calls + 2)

    async def test_observation_failure_does_not_change_answer_and_export_remains_read_only(self) -> None:
        # 故障注入位于已确认的存储公共边界。
        from unittest.mock import AsyncMock
        self.app.state.trajectory_recorder.repository.append = AsyncMock(side_effect=OSError("合成磁盘写入故障"))
        result = await self.client.post("/api/agent-workbench/general-assistant/runs", json={
            "user_goal": "仍要正常回答", "start_new_conversation": True,
        })
        self.assertEqual(result.json()["run"]["status"], "completed")
        run = result.json()["run"]
        calls = len(self.gateway.requests)
        base = f"/api/agent-workbench/general-assistant/conversations/{run['conversation_id']}/trajectory"
        self.assertFalse((await self.client.get(base + "/records")).json()["complete"])
        exported = await self.client.get(base + "/export")
        self.assertEqual(exported.status_code, 200)
        self.assertEqual(exported.headers["x-trajectory-complete"], "false")
        self.assertEqual(len(self.gateway.requests), calls)

    async def test_parallel_same_name_tools_and_reused_native_ids_do_not_cross_conversations(self) -> None:
        gateway = _ParallelToolsGateway()
        app = create_app(app_settings=Settings(project_assets_dir=self.assets / "parallel"),
                         llm_gateway=gateway, knowledge_repository=InMemoryKnowledgeRepository(),
                         graph_checkpointer=InMemorySaver(), graph_store=InMemoryStore(),
                         tool_budget_repository=InMemoryGeneralAgentToolBudgetRepository())
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 先通过公开入口初始化空小说大纲，避免并行读取争用首次建文件。
            self.assertEqual((await client.get("/api/outline")).status_code, 200)
            all_call_ids = set()
            for index in range(2):
                result = await client.post("/api/agent-workbench/general-assistant/runs", json={
                    "user_goal": f"合成并行请求 {index}", "start_new_conversation": True,
                })
                run = result.json()["run"]
                self.assertEqual(run["status"], "completed")
                base = f"/api/agent-workbench/general-assistant/conversations/{run['conversation_id']}/trajectory"
                events = (await client.get(base)).json()["events"]
                starts = [event for event in events if event["kind"] == "tool_started"]
                self.assertEqual(len(starts), 2)
                self.assertEqual(len({event["call_id"] for event in starts}), 2)
                self.assertEqual(len({event["parent_call_id"] for event in starts}), 1)
                ends = {event["call_id"] for event in events if event["kind"] == "tool_completed"}
                self.assertEqual(ends, {event["call_id"] for event in starts},
                                 str([event["data"] for event in events if event["kind"] == "tool_failed"]))
                page = (await client.get(base + "/records", params={"limit": 1})).json()
                self.assertEqual(page["turns"][0]["tools"], 2)
                detail = (await client.get(base + "/records/" + starts[0]["event_id"])).json()
                self.assertEqual(detail["start"]["data"]["arguments"]["limit"], 200)
                parent_end = next(event for event in events if event["kind"] == "model_completed"
                                  and event["call_id"] == starts[0]["parent_call_id"])
                updates = (await client.get(base + "/records", params={"after": parent_end["sequence"]})).json()["records"]
                parent = next(row for row in updates if row["start"]["call_id"] == starts[0]["parent_call_id"])
                self.assertEqual(parent["tool_count"], 2)
                self.assertEqual(parent["tool_names"], ["get_novel_structure"])
                cursor = parent_end["sequence"]
                observed_ids = set()
                for _ in range(len(events)):
                    incremental = (await client.get(base + "/records", params={"after": cursor, "limit": 1})).json()
                    observed_ids.update(row["id"] for row in incremental["records"])
                    if not incremental["records"]:
                        break
                    self.assertGreater(incremental["cursor"], cursor)
                    cursor = incremental["cursor"]
                self.assertTrue({event["event_id"] for event in starts}.issubset(observed_ids))
                calls = {event["call_id"] for event in events if event["kind"] in {"tool_started", "model_started"}}
                self.assertFalse(all_call_ids.intersection(calls))
                all_call_ids.update(calls)
                exported = await client.get(base + "/export")
                self.assertEqual([json.loads(line)["event_id"] for line in exported.text.splitlines()], [event["event_id"] for event in events])

    async def test_cancellation_records_actual_status_and_missing_usage(self) -> None:
        entered = asyncio.Event()

        class BlockingGateway(_DirectAnswerGateway):
            async def complete(self, request):
                entered.set()
                await asyncio.Event().wait()
                return await super().complete(request)

        app = create_app(app_settings=Settings(project_assets_dir=self.assets / "cancel"),
                         llm_gateway=BlockingGateway(), knowledge_repository=InMemoryKnowledgeRepository(),
                         graph_checkpointer=InMemorySaver(), graph_store=InMemoryStore(),
                         tool_budget_repository=InMemoryGeneralAgentToolBudgetRepository())
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            run = (await client.post("/api/agent-workbench/general-assistant/runs/start", json={
                "user_goal": "合成取消请求", "start_new_conversation": True,
            })).json()["run"]
            await asyncio.wait_for(entered.wait(), 5)
            stopped = await client.post(f"/api/agent-workbench/general-assistant/runs/{run['run_id']}/cancel")
            self.assertEqual(stopped.json()["run"]["status"], "cancelled")
            base = f"/api/agent-workbench/general-assistant/conversations/{run['conversation_id']}/trajectory"
            for _ in range(50):
                page = (await client.get(base + "/records")).json()
                if page["turns"][0]["status"] == "cancelled":
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(page["turns"][0]["status"], "cancelled")
            self.assertTrue(page["turns"][0]["complete"])
            self.assertIsNone(page["turns"][0]["input_tokens"])
            self.assertTrue(any(row["end"] and row["end"]["kind"] == "model_stopped" for row in page["records"]))

    async def test_observation_sanitizer_failure_does_not_block_real_execution(self) -> None:
        def broken_sanitizer(value):
            raise ValueError("合成观测加工失败")
        self.gateway.sanitize_observation = broken_sanitizer
        result = await self.client.post("/api/agent-workbench/general-assistant/runs", json={
            "user_goal": "观测加工失败仍可回答", "start_new_conversation": True,
        })
        self.assertEqual(result.json()["run"]["status"], "completed")
        conversation_id = result.json()["run"]["conversation_id"]
        read = await self.client.get(f"/api/agent-workbench/general-assistant/conversations/{conversation_id}/trajectory")
        self.assertFalse(read.json()["complete"])
