"""Right Code 统一 Responses 与 Messages 网关测试。"""

import asyncio
from dataclasses import asdict, replace
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import SecretStr

from taichu.application.contracts.llm import (
    LLMModelAvailability,
    LLMModelManagementError,
    LLMModelManagementPort,
)
from taichu.infrastructure.llm.contracts import (
    LLMMessage,
    LLMRequest,
    LLMToolCall,
    LLMToolDefinition,
    LLMTransportProfile,
)
from taichu.application.models.llm_usage import LLMUsageQuery
from taichu.config import Settings
from taichu.infrastructure.llm.catalog import LLMModelCatalog
from taichu.infrastructure.llm.rightcode import (
    LLMGatewayError,
    RightCodeLLMGateway,
    _anthropic_tool_choice,
    _responses_tool_choice,
)
from taichu.infrastructure.llm_usage import JsonlLLMUsageRepository
from taichu.infrastructure.llm_replays import JsonLLMCallReplayRepository
from taichu.infrastructure.llm.adapter import GatewayChatModel
from taichu.application.trajectory.observer import (
    ObservationScope, TrajectoryRecorder, active_observation,
)
from taichu.infrastructure.trajectory.zstd_repository import ZstdTrajectoryRepository


class RightCodeGatewayTest(unittest.IsolatedAsyncioTestCase):
    async def test_trajectory_retains_partial_stream_when_transport_fails(self) -> None:
        class BrokenStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield 'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"半途输出\\n"}\n\n'.encode()
                raise httpx.ReadError("合成流中断")

        gateway, client, _ = self._gateway(httpx.MockTransport(lambda _: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=BrokenStream())), max_retries=0)
        recorder = TrajectoryRecorder(ZstdTrajectoryRepository(self.assets_root / "trajectory"))
        token = active_observation.set(ObservationScope(recorder, "partial", "partial-run", 1))
        fragments = []
        try:
            with self.assertRaises(Exception):
                async for chunk in GatewayChatModel(gateway, model_id="gpt-5-6-luna").astream([HumanMessage("合成中断请求")]):
                    fragments.append(str(chunk.content))
        finally:
            active_observation.reset(token)
            await client.aclose()
        trace = await recorder.read("partial")
        self.assertEqual("".join(fragments), "半途输出\n")
        self.assertEqual(len([event for event in trace.events if event.kind == "model_started"]), 1)
        self.assertEqual(len([event for event in trace.events if event.kind == "model_failed"]), 1)
        self.assertFalse(any(event.kind == "model_completed" for event in trace.events))
        self.assertTrue(any(event.kind == "wire_stream_chunk" for event in trace.events))

    async def test_trajectory_retains_failed_retry_without_duplicate_usage_or_credentials(self) -> None:
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if len(seen) == 1:
                return httpx.Response(429, json={"error": {"message": "合成限流"}})
            return httpx.Response(200, json={"id": "synthetic-retry",
                "output_text": "合成回答 test-rightcode-key",
                "usage": {"input_tokens": 8, "output_tokens": 3}})

        gateway, client, _ = self._gateway(httpx.MockTransport(handler), max_retries=1)
        recorder = TrajectoryRecorder(ZstdTrajectoryRepository(self.assets_root / "trajectory"))
        token = active_observation.set(ObservationScope(recorder, "synthetic-conversation", "synthetic-run", 1))
        try:
            answer = await GatewayChatModel(gateway, model_id="gpt-5-6-luna").ainvoke([HumanMessage("合成重试请求")])
        finally:
            active_observation.reset(token)
            await client.aclose()
        self.assertIn("test-rightcode-key", str(answer.content))
        trace = await recorder.read("synthetic-conversation")
        self.assertEqual(len([event for event in trace.events if event.kind == "model_started"]), 1)
        completions = [event for event in trace.events if event.kind == "model_completed"]
        self.assertEqual(len(completions), 1)
        self.assertEqual(completions[0].data["response"]["usage"]["output_tokens"], 3)
        attempts = [event for event in trace.events if event.kind == "attempt_started"]
        self.assertEqual(len(attempts), 2)
        self.assertEqual(len({event.call_id for event in attempts}), 1)
        received = [event for event in trace.events if event.kind == "attempt_received"]
        self.assertEqual([event.data["status_code"] for event in received], [429, 200])
        self.assertNotIn("test-rightcode-key", trace.model_dump_json())

    async def test_trajectory_preserves_actual_stream_fragments_and_arrival_times(self) -> None:
        sse = "\n".join([
            'event: response.output_text.delta',
            'data: {"type":"response.output_text.delta","delta":"合成"}', '',
            'event: response.output_text.delta',
            'data: {"type":"response.output_text.delta","delta":"回答"}', '',
            'event: response.completed',
            'data: {"type":"response.completed","response":{"id":"synthetic-stream","output_text":"合成回答","output":[{"type":"reasoning","summary":[{"type":"summary_text","text":"合成推理摘要"}]}],"usage":{"input_tokens":3,"output_tokens":2}}}', '',
        ])
        gateway, client, _ = self._gateway(httpx.MockTransport(
            lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, text=sse)
        ))
        recorder = TrajectoryRecorder(ZstdTrajectoryRepository(self.assets_root / "trajectory"))
        token = active_observation.set(ObservationScope(recorder, "synthetic-conversation", "synthetic-run", 1))
        try:
            chunks = [chunk async for chunk in GatewayChatModel(gateway, model_id="gpt-5-6-luna").astream([HumanMessage("合成流式请求")])]
        finally:
            active_observation.reset(token)
            await client.aclose()
        self.assertEqual("".join(str(chunk.content) for chunk in chunks), "合成回答")
        trace = await recorder.read("synthetic-conversation")
        deltas = [event for event in trace.events if event.kind == "stream_chunk" and event.data["event"]["event_type"] == "text_delta"]
        self.assertEqual([event.data["event"]["delta"] for event in deltas], ["合成", "回答"])
        self.assertTrue(all(event.recorded_at for event in deltas))
        wire = [event for event in trace.events if event.kind == "wire_stream_chunk"]
        self.assertIn("合成推理摘要", json.dumps([event.data for event in wire], ensure_ascii=False))
        self.assertEqual(len([event for event in trace.events if event.kind == "model_completed"]), 1)

    async def test_trajectory_contains_actual_http_business_request_without_authentication(self) -> None:
        received = []

        def handler(request: httpx.Request) -> httpx.Response:
            received.append(json.loads(request.content))
            return httpx.Response(200, json={
                "id": "synthetic-request", "output_text": "合成回答",
                "usage": {"input_tokens": 8, "output_tokens": 3},
            })

        gateway, client, _ = self._gateway(httpx.MockTransport(handler))
        recorder = TrajectoryRecorder(ZstdTrajectoryRepository(self.assets_root / "trajectory"))
        token = active_observation.set(ObservationScope(recorder, "synthetic-conversation", "synthetic-run", 1))
        try:
            model = GatewayChatModel(gateway, model_id="gpt-5-6-luna")
            await model.ainvoke([SystemMessage("稳定约束"), HumanMessage("合成请求")])
        finally:
            active_observation.reset(token)
            await client.aclose()
        trace = await recorder.read("synthetic-conversation")
        attempts = [event for event in trace.events if event.kind == "attempt_started"]
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0].data["wire_request"], received[0])
        self.assertNotIn("Authorization", attempts[0].model_dump_json())
        self.assertNotIn("x-api-key", attempts[0].model_dump_json())

    async def asyncSetUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.assets_root = Path(self.temporary_directory.name)

    async def asyncTearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_named_tool_choice_uses_provider_native_parameter(self) -> None:
        self.assertEqual(
            _responses_tool_choice("GeneralAgentPlanDraft"),
            {"type": "function", "name": "GeneralAgentPlanDraft"},
        )
        self.assertEqual(
            _anthropic_tool_choice("GeneralAgentPlanDraft"),
            {"type": "tool", "name": "GeneralAgentPlanDraft"},
        )

    async def test_gateway_structurally_implements_model_management_port(
        self,
    ) -> None:
        gateway, client, _ = self._gateway(
            httpx.MockTransport(lambda _: httpx.Response(200))
        )
        try:
            self.assertIsInstance(gateway, LLMModelManagementPort)
            self.assertIsInstance(
                gateway.availability_for("deepseek-v4-pro"),
                LLMModelAvailability,
            )
        finally:
            await client.aclose()

    async def test_gateway_only_closes_the_http_client_it_owns(self) -> None:
        settings = _settings(self.assets_root)
        owned_gateway = RightCodeLLMGateway(
            settings,
            LLMModelCatalog(settings),
            JsonlLLMUsageRepository(self.assets_root),
        )
        owned_client = owned_gateway._client
        self.assertFalse(owned_client.is_closed)
        await owned_gateway.aclose()
        self.assertTrue(owned_client.is_closed)

        external_client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200))
        )
        external_gateway = RightCodeLLMGateway(
            settings,
            LLMModelCatalog(settings),
            JsonlLLMUsageRepository(self.assets_root),
            client=external_client,
        )
        try:
            await external_gateway.aclose()
            self.assertFalse(external_client.is_closed)
        finally:
            await external_client.aclose()

    def test_catalog_has_ten_unique_models_and_one_default(self) -> None:
        catalog = LLMModelCatalog(_settings(self.assets_root))
        profiles = catalog.list_models()
        self.assertEqual(len(profiles), 10)
        self.assertEqual(len({item.id for item in profiles}), 10)
        self.assertEqual(
            [item.id for item in profiles if item.is_default],
            ["deepseek-flash"],
        )
        self.assertEqual(
            catalog.resolve("deepseek-v4-pro").upstream_model,
            "deepseek-v4-pro",
        )
        self.assertEqual(
            catalog.resolve("claude-opus-4-6").wire_protocol,
            "anthropic_messages",
        )
        self.assertEqual(
            catalog.resolve("deepseek-v4-pro").wire_protocol,
            "anthropic_messages",
        )
        self.assertTrue(all(type(item) is not LLMTransportProfile for item in profiles))
        self.assertTrue(all("base_url_key" not in asdict(item) for item in profiles))
        self.assertEqual(
            catalog.resolve("deepseek-v4-pro").base_url_key,
            "RIGHTCODE_DEEPSEEK_ANTHROPIC_BASE_URL",
        )
        self.assertFalse(catalog.resolve("deepseek-flash").upstream_verified)
        self.assertTrue(all(item.upstream_verified for item in profiles if item.id != "deepseek-flash"))

    def test_deepseek_official_catalog_defaults_to_v41_flash(self) -> None:
        catalog = LLMModelCatalog(_settings(self.assets_root))
        profiles = catalog.list_models("deepseek_official")

        self.assertEqual(
            [item.id for item in profiles],
            ["deepseek-flash", "deepseek-v4-pro"],
        )
        self.assertEqual(
            [item.id for item in profiles if item.is_default],
            ["deepseek-flash"],
        )
        self.assertEqual(
            [item.display_name for item in profiles],
            ["DeepSeek V4.1 Flash", "DeepSeek V4 Pro"],
        )
        self.assertEqual(
            catalog.default_model_id_for("deepseek_official"),
            "deepseek-flash",
        )
        flash, pro = profiles
        self.assertEqual(flash.input_price_per_million, Decimal("0.3"))
        self.assertEqual(flash.cached_input_price_per_million, Decimal("0.006"))
        self.assertEqual(flash.output_price_per_million, Decimal("1.2"))
        self.assertEqual(flash.currency, "USD")
        self.assertEqual(flash.reasoning_output_price_per_million, Decimal("1.2"))
        self.assertEqual(pro.input_price_per_million, Decimal("9.0"))
        self.assertEqual(pro.cached_input_price_per_million, Decimal("0.30"))
        self.assertEqual(pro.output_price_per_million, Decimal("27.0"))
        self.assertEqual(pro.reasoning_output_price_per_million, Decimal("27.0"))
        self.assertEqual(pro.currency, "CNY")

    def test_unknown_model_is_rejected_with_stable_chinese_error(self) -> None:
        catalog = LLMModelCatalog(_settings(self.assets_root))
        with self.assertRaises(LLMModelManagementError) as context:
            catalog.resolve("unknown")
        self.assertEqual(context.exception.code, "LLM_MODEL_UNKNOWN")
        self.assertIn("模型不存在", context.exception.message)

    async def test_model_selection_changes_upstream_and_is_concurrency_safe(
        self,
    ) -> None:
        requested_models: list[str] = []
        requested_urls: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            requested_models.append(payload["model"])
            requested_urls.append(str(request.url))
            await asyncio.sleep(0)
            if request.url.path.endswith("/v1/messages"):
                return httpx.Response(
                    200,
                    json={
                        "id": "claude-request",
                        "content": [{"type": "text", "text": payload["model"]}],
                        "usage": {"input_tokens": 2, "output_tokens": 1},
                    },
                )
            return httpx.Response(
                200,
                json={
                    "id": f"request-{len(requested_models)}",
                    "output_text": payload["model"],
                    "usage": {"input_tokens": 2, "output_tokens": 1},
                },
            )

        gateway, client, _ = self._gateway(httpx.MockTransport(handler))
        try:
            responses = await asyncio.gather(
                gateway.complete(_request("gpt-5-6-luna")),
                gateway.complete(_request("claude-opus-4-6")),
                gateway.complete(_request("deepseek-v4-pro")),
            )
        finally:
            await client.aclose()
        self.assertEqual(
            set(requested_models),
            {"gpt-5.6-luna", "claude-opus-4-6", "deepseek-v4-pro"},
        )
        self.assertEqual(
            set(requested_urls),
            {
                "https://www.rightapi.ai/codex/v1/responses",
                "https://www.rightapi.ai/claude/v1/messages",
                "https://rightapi.ai/deepseek/anthropic/v1/messages",
            },
        )
        self.assertEqual(
            {item.upstream_model for item in responses}, set(requested_models)
        )

    async def test_responses_usage_and_actual_cost_are_normalized(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "id": "provider-request",
                    "output": [{"content": [{"type": "output_text", "text": "完成"}]}],
                    "usage": {
                        "input_tokens": 12,
                        "input_tokens_details": {"cached_tokens": 3},
                        "output_tokens": 5,
                        "output_tokens_details": {"reasoning_tokens": 2},
                        "total_tokens": 17,
                    },
                    "billing": {"amount": "0.1234", "currency": "CNY"},
                },
            )

        gateway, client, repository = self._gateway(httpx.MockTransport(handler))
        try:
            response = await gateway.complete(_request("gpt-5-6-luna"))
        finally:
            await client.aclose()
        self.assertEqual(response.text, "完成")
        self.assertEqual(response.usage.cached_input_tokens, 3)
        self.assertEqual(response.usage.reasoning_tokens, 2)
        self.assertEqual(response.cost.amount, Decimal("0.1234"))
        self.assertEqual(response.cost.kind, "actual")
        record = await repository.get(response.call_id or "")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.provider_request_id, "provider-request")
        self.assertEqual(record.status_code, 200)
        self.assertEqual(record.content_block_types, ["output_text"])

    async def test_anthropic_response_without_body_id_uses_header_diagnostics(
        self,
    ) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"x-request-id": "header-request-id"},
                json={
                    "content": [{"type": "text", "text": "可用"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 4, "output_tokens": 2},
                },
            )

        gateway, client, repository = self._gateway(
            httpx.MockTransport(handler),
            fallback_key="test-deepseek-key",
            enforce_active_provider=True,
        )
        gateway.set_active_provider("deepseek_official")
        try:
            response = await gateway.complete(_request("deepseek-flash"))
        finally:
            await client.aclose()

        self.assertEqual(response.text, "可用")
        self.assertEqual(response.provider_request_id, "header-request-id")
        record = await repository.get(response.call_id or "")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.status_code, 200)
        self.assertEqual(record.provider_request_id, "header-request-id")
        self.assertEqual(record.content_block_types, ["text"])

    async def test_run_call_replay_saves_redacted_messages_and_response(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "id": "provider-request",
                    "output_text": "Authorization: Bearer response-secret-token",
                    "usage": {"input_tokens": 8, "output_tokens": 3},
                },
            )

        gateway, client, _ = self._gateway(httpx.MockTransport(handler))
        request = LLMRequest(
            model_id="gpt-5-6-luna",
            messages=(
                LLMMessage(role="system", content="系统约束"),
                LLMMessage(role="user", content="api_key=sk-request-secret-token"),
            ),
            task_type="general_agent",
            task_name="general_writing_orchestrator.plan",
            run_id="general_run_20260721_120000_abc123",
            context_snapshot_id="context_20260721_120000_abc12345",
        )
        try:
            response = await gateway.complete(request)
        finally:
            await client.aclose()

        repository = JsonLLMCallReplayRepository(self.assets_root)
        records = await repository.list_for_run(request.run_id or "")
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.call_id, response.call_id)
        self.assertEqual(record.context_snapshot_id, request.context_snapshot_id)
        self.assertEqual(record.messages[1].content, "api_key=[已脱敏]")
        self.assertEqual(record.response_text, "Authorization: Bearer [已脱敏]")
        self.assertEqual(record.redaction_count, 2)
        self.assertEqual(record.wire_request_body["instructions"], "系统约束")
        self.assertEqual(
            record.wire_request_body["input"][0]["content"][0]["text"],
            "api_key=[已脱敏]",
        )
        self.assertFalse(record.wire_request_body["stream"])
        raw = next(
            (self.assets_root / "derived" / "llm_call_replays").glob("*.json")
        ).read_text(encoding="utf-8")
        self.assertNotIn("request-secret-token", raw)
        self.assertNotIn("response-secret-token", raw)
        await repository.delete_run(request.run_id or "")
        self.assertEqual(await repository.list_for_run(request.run_id or ""), [])

    async def test_gpt_5_6_responses_omits_unsupported_request_options(self) -> None:
        payloads: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payloads.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "id": "provider-request",
                    "output_text": "{}",
                    "usage": {"input_tokens": 8, "output_tokens": 3},
                },
            )

        gateway, client, _ = self._gateway(httpx.MockTransport(handler))
        request = replace(
            _request("gpt-5-6-terra"),
            temperature=0.1,
        )
        try:
            await gateway.complete(request)
        finally:
            await client.aclose()

        self.assertNotIn("temperature", payloads[0])
        self.assertNotIn("text", payloads[0])

    async def test_responses_sse_stream_is_normalized(self) -> None:
        sse = "\n".join(
            [
                "event: response.output_text.delta",
                'data: {"type":"response.output_text.delta","delta":"秦浩"}',
                "",
                "event: response.output_text.delta",
                'data: {"type":"response.output_text.delta","delta":"轩"}',
                "",
                "event: response.completed",
                (
                    'data: {"type":"response.completed","response":'
                    '{"id":"stream-request","output_text":"秦浩轩",'
                    '"usage":{"input_tokens":3,"output_tokens":2,'
                    '"total_tokens":5}}}'
                ),
                "",
            ]
        )

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=sse.encode(),
            )

        gateway, client, _ = self._gateway(httpx.MockTransport(handler))
        try:
            events = [event async for event in gateway.stream(_request("gpt-5-6-sol"))]
        finally:
            await client.aclose()
        self.assertEqual(
            "".join(item.delta for item in events if item.event_type == "text_delta"),
            "秦浩轩",
        )
        completed = next(item for item in events if item.event_type == "completed")
        assert completed.response is not None
        self.assertEqual(completed.response.usage.total_tokens, 5)

    async def test_claude_messages_response_is_normalized(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["path"] = request.url.path
            captured["payload"] = json.loads(request.content)
            captured["has_anthropic_version"] = "anthropic-version" in request.headers
            return httpx.Response(
                200,
                json={
                    "id": "message-request",
                    "content": [{"type": "text", "text": "完成"}],
                    "stop_reason": "end_turn",
                    "usage": {
                        "input_tokens": 7,
                        "cache_read_input_tokens": 3,
                        "output_tokens": 2,
                    },
                },
            )

        gateway, client, _ = self._gateway(httpx.MockTransport(handler))
        try:
            response = await gateway.complete(_request("claude-opus-4-6"))
        finally:
            await client.aclose()
        payload = captured["payload"]
        assert isinstance(payload, dict)
        self.assertEqual(captured["path"], "/claude/v1/messages")
        self.assertTrue(captured["has_anthropic_version"])
        self.assertEqual(payload["system"], "系统约束")
        self.assertEqual(payload["messages"][0]["role"], "user")
        self.assertEqual(response.text, "完成")
        self.assertEqual(response.usage.cached_input_tokens, 3)
        self.assertEqual(response.usage.total_tokens, 12)
        self.assertEqual(response.finish_reason, "end_turn")

    async def test_claude_messages_sse_stream_is_normalized(self) -> None:
        sse = "\n".join(
            [
                "event: message_start",
                'data: {"type":"message_start","message":{"id":"msg-stream","usage":{"input_tokens":4,"cache_read_input_tokens":3}}}',
                "",
                "event: content_block_delta",
                'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"秦浩"}}',
                "",
                "event: content_block_delta",
                'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"轩"}}',
                "",
                "event: message_delta",
                'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":2}}',
                "",
                "event: message_stop",
                'data: {"type":"message_stop"}',
                "",
            ]
        )

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=sse.encode(),
            )

        gateway, client, _ = self._gateway(httpx.MockTransport(handler))
        try:
            events = [
                event async for event in gateway.stream(_request("claude-sonnet-4-6"))
            ]
        finally:
            await client.aclose()
        self.assertEqual(
            "".join(item.delta for item in events if item.event_type == "text_delta"),
            "秦浩轩",
        )
        completed = next(item for item in events if item.event_type == "completed")
        assert completed.response is not None
        self.assertEqual(completed.response.provider_request_id, "msg-stream")
        self.assertEqual(completed.response.usage.total_tokens, 9)

    async def test_anthropic_tool_use_sse_stream_is_normalized(self) -> None:
        sse = "\n".join(
            [
                "event: message_start",
                'data: {"type":"message_start","message":{"id":"msg-tools","usage":{"input_tokens":5,"cache_read_input_tokens":2}}}',
                "",
                "event: content_block_start",
                'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
                "",
                "event: content_block_delta",
                'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"先说明。"}}',
                "",
                "event: content_block_stop",
                'data: {"type":"content_block_stop","index":0}',
                "",
                "event: content_block_start",
                'data: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"toolu_1","name":"write_text","input":{}}}',
                "",
                "event: content_block_delta",
                'data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"text\\":\\"秦浩"}}',
                "",
                "event: content_block_delta",
                'data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"轩\\"}"}}',
                "",
                "event: content_block_stop",
                'data: {"type":"content_block_stop","index":1}',
                "",
                "event: content_block_start",
                'data: {"type":"content_block_start","index":2,"content_block":{"type":"tool_use","id":"toolu_2","name":"record_note","input":{}}}',
                "",
                "event: content_block_delta",
                'data: {"type":"content_block_delta","index":2,"delta":{"type":"input_json_delta","partial_json":"{\\"note\\":\\"已核对\\"}"}}',
                "",
                "event: content_block_stop",
                'data: {"type":"content_block_stop","index":2}',
                "",
                "event: message_delta",
                'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":7}}',
                "",
                "event: message_stop",
                'data: {"type":"message_stop"}',
                "",
            ]
        )

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=sse.encode(),
            )

        gateway, client, _ = self._gateway(httpx.MockTransport(handler))
        try:
            events = [
                event async for event in gateway.stream(_request("deepseek-v4-pro"))
            ]
        finally:
            await client.aclose()

        self.assertFalse(any(item.event_type == "failed" for item in events))
        self.assertEqual(
            "".join(
                item.delta for item in events if item.event_type == "text_delta"
            ),
            "先说明。",
        )
        chunks = [
            item.tool_call_chunk
            for item in events
            if item.event_type == "tool_call_delta"
        ]
        self.assertEqual(chunks[0].call_id, "toolu_1")
        self.assertEqual(chunks[0].name, "write_text")
        self.assertEqual(chunks[0].index, 0)
        self.assertEqual(
            "".join(item.arguments_delta for item in chunks if item.index == 0),
            '{"text":"秦浩轩"}',
        )
        self.assertEqual(chunks[3].call_id, "toolu_2")
        self.assertEqual(chunks[3].index, 1)
        completed = next(item for item in events if item.event_type == "completed")
        assert completed.response is not None
        self.assertEqual(
            [item.call_id for item in completed.response.tool_calls],
            ["toolu_1", "toolu_2"],
        )
        self.assertEqual(
            [json.loads(item.arguments_json) for item in completed.response.tool_calls],
            [{"text": "秦浩轩"}, {"note": "已核对"}],
        )
        self.assertEqual(completed.response.finish_reason, "tool_use")
        self.assertEqual(completed.response.usage.total_tokens, 14)

    async def test_responses_function_arguments_sse_stream_is_normalized(self) -> None:
        sse = "\n".join(
            [
                "event: response.output_item.added",
                'data: {"type":"response.output_item.added","output_index":0,"item":{"type":"function_call","id":"fc_1","call_id":"call_1","name":"write_text","arguments":""}}',
                "",
                "event: response.function_call_arguments.delta",
                'data: {"type":"response.function_call_arguments.delta","output_index":0,"item_id":"fc_1","delta":"{\\"text\\":\\"秦浩"}',
                "",
                "event: response.function_call_arguments.delta",
                'data: {"type":"response.function_call_arguments.delta","output_index":0,"item_id":"fc_1","delta":"轩\\"}"}',
                "",
                "event: response.function_call_arguments.done",
                'data: {"type":"response.function_call_arguments.done","output_index":0,"item_id":"fc_1","arguments":"{\\"text\\":\\"秦浩轩\\"}"}',
                "",
                "event: response.completed",
                'data: {"type":"response.completed","response":{"id":"response-tools","status":"completed","output":[{"type":"function_call","id":"fc_1","call_id":"call_1","name":"write_text","arguments":"{\\"text\\":\\"秦浩轩\\"}"}],"usage":{"input_tokens":5,"output_tokens":4,"total_tokens":9}}}',
                "",
            ]
        )

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=sse.encode(),
            )

        gateway, client, _ = self._gateway(httpx.MockTransport(handler))
        try:
            events = [
                event async for event in gateway.stream(_request("gpt-5-6-sol"))
            ]
        finally:
            await client.aclose()

        chunks = [
            item.tool_call_chunk
            for item in events
            if item.event_type == "tool_call_delta"
        ]
        self.assertEqual(chunks[0].call_id, "call_1")
        self.assertEqual(chunks[0].name, "write_text")
        self.assertEqual(
            "".join(item.arguments_delta for item in chunks),
            '{"text":"秦浩轩"}',
        )
        completed = next(item for item in events if item.event_type == "completed")
        assert completed.response is not None
        self.assertEqual(completed.response.tool_calls[0].call_id, "call_1")
        self.assertEqual(
            json.loads(completed.response.tool_calls[0].arguments_json),
            {"text": "秦浩轩"},
        )

    async def test_deepseek_uses_rightcode_anthropic_channel(self) -> None:
        captured_path = ""

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_path
            captured_path = request.url.path
            return httpx.Response(
                200,
                json={
                    "id": "deepseek-message",
                    "content": [{"type": "text", "text": "可用"}],
                    "usage": {"input_tokens": 3, "output_tokens": 1},
                },
            )

        gateway, client, _ = self._gateway(httpx.MockTransport(handler))
        try:
            response = await gateway.complete(_request("deepseek-v4-pro"))
        finally:
            await client.aclose()
        self.assertEqual(captured_path, "/deepseek/anthropic/v1/messages")
        self.assertEqual(response.text, "可用")

    async def test_deepseek_structured_auto_choice_enables_thinking(self) -> None:
        captured_payload: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_payload.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "id": "deepseek-structured",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_plan",
                            "name": "GeneralAgentPlanDraft",
                            "input": {"nodes": []},
                        }
                    ],
                    "stop_reason": "tool_use",
                    "usage": {"input_tokens": 8, "output_tokens": 4},
                },
            )

        definition = LLMToolDefinition(
            name="GeneralAgentPlanDraft",
            description="返回结构化执行计划。",
            parameters={"type": "object", "properties": {}},
        )
        gateway, client, _ = self._gateway(httpx.MockTransport(handler))
        try:
            response = await gateway.complete(
                replace(
                    _request("deepseek-v4-pro"),
                    tools=(definition,),
                    tool_choice="auto",
                )
            )
        finally:
            await client.aclose()

        self.assertEqual(captured_payload["thinking"], {"type": "enabled"})
        self.assertEqual(
            captured_payload["tool_choice"],
            {"type": "auto"},
        )
        self.assertEqual(response.tool_calls[0].name, "GeneralAgentPlanDraft")

    async def test_deepseek_auto_tool_choice_explicitly_enables_thinking(self) -> None:
        captured_payload: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_payload.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "id": "deepseek-auto",
                    "content": [{"type": "text", "text": "无需调用工具"}],
                    "usage": {"input_tokens": 5, "output_tokens": 2},
                },
            )

        definition = LLMToolDefinition(
            name="get_weather",
            description="查询天气。",
            parameters={"type": "object", "properties": {}},
        )
        gateway, client, _ = self._gateway(httpx.MockTransport(handler))
        try:
            await gateway.complete(
                replace(
                    _request("deepseek-v4-pro"),
                    tools=(definition,),
                    tool_choice="auto",
                )
            )
        finally:
            await client.aclose()

        self.assertEqual(captured_payload["thinking"], {"type": "enabled"})
        self.assertEqual(captured_payload["tool_choice"], {"type": "auto"})

    async def test_official_deepseek_rag_judge_uses_enabled_thinking(self) -> None:
        captured_payload: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_payload.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "content": [{"type": "text", "text": '{"score": 1}'}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 8, "output_tokens": 4},
                },
            )

        gateway, client, _ = self._gateway(
            httpx.MockTransport(handler),
            fallback_key="official-key",
            enforce_active_provider=True,
        )
        gateway.set_active_provider("deepseek_official")
        try:
            await gateway.complete(
                LLMRequest(
                    model_id="deepseek-flash",
                    messages=(LLMMessage(role="user", content="评测"),),
                    task_type="rag_evaluation_judge",
                    task_name="RAG 语义质量评测",
                    max_output_tokens=100_000,
                )
            )
        finally:
            await client.aclose()

        self.assertEqual(captured_payload["thinking"], {"type": "enabled"})
        self.assertEqual(captured_payload["max_tokens"], 100_000)

    async def test_thinking_and_signature_survive_messages_stream_and_tool_continuation(self) -> None:
        thinking = {"type": "thinking", "thinking": "测试推理", "signature": "测试签名"}
        for stream in (False, True):
            payloads = []

            def handler(request):
                payloads.append(json.loads(request.content))
                if not json.loads(request.content)["stream"]:
                    return httpx.Response(200, json={
                        "content": [thinking, {"type": "tool_use", "id": "calc", "name": "calculate", "input": {}}],
                        "stop_reason": "tool_use", "usage": {"input_tokens": 5, "output_tokens": 8},
                    })
                events = [
                    ("message_start", {"message": {"id": "msg", "usage": {"input_tokens": 5}}}),
                    ("content_block_start", {"index": 0, "content_block": {"type": "thinking", "thinking": ""}}),
                    ("content_block_delta", {"index": 0, "delta": {"type": "thinking_delta", "thinking": "测试推理"}}),
                    ("content_block_delta", {"index": 0, "delta": {"type": "signature_delta", "signature": "测试签名"}}),
                    ("content_block_start", {"index": 1, "content_block": {"type": "tool_use", "id": "calc", "name": "calculate", "input": {}}}),
                    ("content_block_delta", {"index": 1, "delta": {"type": "input_json_delta", "partial_json": "{}"}}),
                    ("message_delta", {"delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 8}}),
                    ("message_stop", {}),
                ]
                body = "".join(f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n" for name, data in events)
                return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())

            gateway, client, _ = self._gateway(httpx.MockTransport(handler))
            model = GatewayChatModel(gateway, model_id="deepseek-flash")
            try:
                messages = [HumanMessage(content="计算。")]
                if stream:
                    chunks = [chunk async for chunk in model.astream(messages)]
                    answer = chunks[0]
                    for chunk in chunks[1:]:
                        answer += chunk
                else:
                    answer = await model.ainvoke(messages)
                self.assertIn(thinking, answer.content)
                self.assertEqual(answer.text, "")
                from langchain_core.messages import ToolMessage
                await model.ainvoke([*messages, answer, ToolMessage(content="391", tool_call_id="calc")])
                blocks = payloads[-1]["messages"][1]["content"]
                self.assertEqual(blocks[0], thinking)
                self.assertEqual(blocks[1]["type"], "tool_use")
                self.assertEqual(blocks[1]["id"], "calc")
                self.assertEqual(payloads[-1]["thinking"], {"type": "enabled"})
            finally:
                await client.aclose()

    async def test_thinking_only_max_tokens_is_reported_as_truncated(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "id": "official-truncated",
                    "content": [{"type": "thinking", "thinking": "尚未完成"}],
                    "stop_reason": "max_tokens",
                    "usage": {"input_tokens": 10, "output_tokens": 2_048},
                },
            )

        gateway, client, _ = self._gateway(
            httpx.MockTransport(handler), fallback_key="test-deepseek-key"
        )
        gateway.set_active_provider("deepseek_official")
        try:
            with self.assertRaises(LLMGatewayError) as context:
                await gateway.complete(_request("deepseek-v4-pro"))
        finally:
            await client.aclose()

        self.assertEqual(context.exception.code, "LLM_OUTPUT_TRUNCATED")

    async def test_deepseek_probe_reserves_reasoning_output_budget(self) -> None:
        captured_max_tokens = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_max_tokens
            payload = json.loads(request.content)
            captured_max_tokens = payload["max_tokens"]
            return httpx.Response(
                200,
                json={
                    "id": "deepseek-probe",
                    "content": [{"type": "text", "text": "可用"}],
                    "usage": {"input_tokens": 3, "output_tokens": 1},
                },
            )

        gateway, client, _ = self._gateway(httpx.MockTransport(handler))
        try:
            state = await gateway.probe_model("deepseek-v4-pro")
        finally:
            await client.aclose()
        self.assertEqual(captured_max_tokens, 1024)
        self.assertEqual(state.availability, "available")
        self.assertEqual(state.requested_provider, "rightcode")
        self.assertEqual(state.requested_model_id, "deepseek-v4-pro")
        self.assertEqual(state.actual_provider, "rightcode")
        self.assertEqual(state.actual_model_id, "deepseek-v4-pro")
        self.assertFalse(state.fallback_used)
        self.assertIsNone(state.fallback_from_provider)
        self.assertEqual(state.wire_protocol, "anthropic_messages")
        self.assertEqual(state.provider_request_id, "deepseek-probe")

    async def test_probe_does_not_use_official_fallback_when_rightcode_fails(
        self,
    ) -> None:
        requested_hosts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested_hosts.append(request.url.host)
            if request.url.host == "rightapi.ai":
                raise httpx.ConnectError("RightCode 不可达", request=request)
            return httpx.Response(
                200,
                json={
                    "id": "deepseek-official-must-not-be-used",
                    "content": [{"type": "text", "text": "官方可用"}],
                    "usage": {"input_tokens": 3, "output_tokens": 1},
                },
            )

        gateway, client, repository = self._gateway(
            httpx.MockTransport(handler),
            fallback_key="test-deepseek-key",
        )
        try:
            state = await gateway.probe_model("deepseek-v4-pro")
        finally:
            await client.aclose()

        self.assertEqual(state.availability, "unavailable")
        self.assertEqual(requested_hosts, ["rightapi.ai"])
        self.assertEqual(state.requested_provider, "rightcode")
        self.assertEqual(state.requested_model_id, "deepseek-v4-pro")
        self.assertEqual(state.actual_provider, "rightcode")
        self.assertEqual(state.actual_model_id, "deepseek-v4-pro")
        self.assertFalse(state.fallback_used)
        self.assertIsNone(state.fallback_from_provider)
        self.assertEqual(state.wire_protocol, "anthropic_messages")
        self.assertIsNone(state.provider_request_id)
        records = await repository.list_calls(LLMUsageQuery())
        self.assertEqual(records.total, 1)
        self.assertEqual(records.items[0].provider, "rightcode")
        self.assertIsNone(records.items[0].fallback_from_provider)
        self.assertEqual(records.items[0].status, "failed")

    async def test_network_failure_falls_back_to_official_deepseek(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.host == "rightapi.ai":
                raise httpx.ConnectError("RightCode 不可达", request=request)
            return httpx.Response(
                200,
                json={
                    "id": "deepseek-official-request",
                    "content": [{"type": "text", "text": "官方降级成功"}],
                    "usage": {"input_tokens": 6, "output_tokens": 3},
                },
            )

        gateway, client, repository = self._gateway(
            httpx.MockTransport(handler),
            fallback_key="test-deepseek-key",
        )
        request = replace(
            _request("deepseek-v4-pro"),
            run_id="general_run_fallback_test",
        )
        try:
            response = await gateway.complete(request)
        finally:
            await client.aclose()

        self.assertEqual(response.text, "官方降级成功")
        self.assertEqual(
            [request.url.host for request in requests],
            ["rightapi.ai", "api.deepseek.com"],
        )
        self.assertEqual(requests[1].headers["x-api-key"], "test-deepseek-key")
        payload = json.loads(requests[1].content)
        self.assertEqual(payload["model"], "deepseek-flash")
        records = await repository.list_calls(LLMUsageQuery())
        self.assertEqual(records.total, 1)
        self.assertEqual(records.items[0].provider, "deepseek_official")
        self.assertEqual(records.items[0].fallback_from_provider, "rightcode")
        replays = await JsonLLMCallReplayRepository(self.assets_root).list_for_run(
            "general_run_fallback_test"
        )
        self.assertEqual(replays[0].provider, "deepseek_official")
        self.assertEqual(replays[0].fallback_from_provider, "rightcode")

    async def test_authentication_failure_does_not_fall_back(self) -> None:
        requested_hosts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested_hosts.append(request.url.host)
            return httpx.Response(403, text="拒绝访问")

        gateway, client, repository = self._gateway(
            httpx.MockTransport(handler),
            fallback_key="test-deepseek-key",
        )
        try:
            with self.assertRaises(LLMGatewayError) as context:
                await gateway.complete(_request("deepseek-v4-pro"))
        finally:
            await client.aclose()

        self.assertEqual(context.exception.code, "LLM_MODEL_FORBIDDEN")
        self.assertEqual(requested_hosts, ["rightapi.ai"])
        records = await repository.list_calls(LLMUsageQuery())
        self.assertEqual(records.items[0].provider, "rightcode")
        self.assertIsNone(records.items[0].fallback_from_provider)

    async def test_official_balance_failure_has_explicit_error(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                402,
                json={
                    "error": {
                        "message": "Insufficient Balance",
                        "code": "invalid_request_error",
                    }
                },
            )

        gateway, client, repository = self._gateway(
            httpx.MockTransport(handler),
            fallback_key="test-deepseek-key",
            enforce_active_provider=True,
        )
        gateway.set_active_provider("deepseek_official")
        try:
            with self.assertRaises(LLMGatewayError) as context:
                await gateway.complete(_request("deepseek-flash"))
        finally:
            await client.aclose()

        self.assertEqual(context.exception.code, "LLM_INSUFFICIENT_BALANCE")
        self.assertEqual(
            context.exception.message,
            "模型供应商账户余额不足，请充值后重试。",
        )
        records = await repository.list_calls(LLMUsageQuery())
        self.assertEqual(records.items[0].provider, "deepseek_official")
        self.assertEqual(records.items[0].error_code, "LLM_INSUFFICIENT_BALANCE")
        self.assertEqual(records.items[0].status_code, 402)
        self.assertIn("Insufficient Balance", records.items[0].error_summary or "")

    async def test_stream_network_failure_falls_back_before_output(self) -> None:
        requested_hosts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested_hosts.append(request.url.host)
            if request.url.host == "rightapi.ai":
                raise httpx.ConnectError("RightCode 不可达", request=request)
            return httpx.Response(
                200,
                json={
                    "id": "deepseek-official-stream-fallback",
                    "content": [{"type": "text", "text": "降级后的完整回答"}],
                    "usage": {"input_tokens": 8, "output_tokens": 4},
                },
            )

        gateway, client, repository = self._gateway(
            httpx.MockTransport(handler),
            fallback_key="test-deepseek-key",
        )
        try:
            events = [
                event async for event in gateway.stream(_request("deepseek-v4-pro"))
            ]
        finally:
            await client.aclose()

        self.assertEqual(requested_hosts, ["rightapi.ai", "api.deepseek.com"])
        self.assertEqual(
            "".join(item.delta for item in events if item.event_type == "text_delta"),
            "降级后的完整回答",
        )
        completed = next(item for item in events if item.event_type == "completed")
        assert completed.response is not None
        self.assertEqual(completed.response.text, "降级后的完整回答")
        records = await repository.list_calls(LLMUsageQuery())
        self.assertEqual(records.items[0].provider, "deepseek_official")
        self.assertEqual(records.items[0].fallback_from_provider, "rightcode")

    async def test_responses_native_tool_call_round_trip_uses_call_id(self) -> None:
        payloads: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payloads.append(json.loads(request.content))
            if len(payloads) == 1:
                return httpx.Response(
                    200,
                    json={
                        "id": "response-tool-call",
                        "output": [
                            {
                                "type": "function_call",
                                "call_id": "call-weather-1",
                                "name": "get_weather",
                                "arguments": '{"city":"北京"}',
                            }
                        ],
                        "usage": {"input_tokens": 4, "output_tokens": 2},
                    },
                )
            return httpx.Response(
                200,
                json={
                    "id": "response-final",
                    "output_text": "北京晴。",
                    "usage": {"input_tokens": 8, "output_tokens": 3},
                },
            )

        gateway, client, _ = self._gateway(httpx.MockTransport(handler))
        definition = LLMToolDefinition(
            name="get_weather",
            description="查询天气",
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
                "additionalProperties": False,
            },
        )
        first_request = replace(
            _request("gpt-5-6-luna"),
            tools=(definition,),
        )
        try:
            first = await gateway.complete(first_request)
            second = await gateway.complete(
                replace(
                    first_request,
                    messages=(
                        *first_request.messages,
                        LLMMessage(role="assistant", tool_calls=first.tool_calls),
                        LLMMessage(
                            role="tool",
                            content='{"temperature":26}',
                            tool_call_id="call-weather-1",
                            tool_name="get_weather",
                        ),
                    ),
                )
            )
        finally:
            await client.aclose()

        self.assertEqual(first.tool_calls[0].call_id, "call-weather-1")
        self.assertEqual(second.text, "北京晴。")
        self.assertEqual(payloads[0]["tools"][0]["name"], "get_weather")
        self.assertEqual(payloads[0]["tool_choice"], "auto")
        self.assertIn(
            {
                "type": "function_call_output",
                "call_id": "call-weather-1",
                "output": '{"temperature":26}',
            },
            payloads[1]["input"],
        )

    async def test_anthropic_native_tool_blocks_are_normalized(self) -> None:
        payloads: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payloads.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "id": "message-tool-call",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "get_weather",
                            "input": {"city": "北京"},
                        }
                    ],
                    "stop_reason": "tool_use",
                    "usage": {"input_tokens": 4, "output_tokens": 2},
                },
            )

        gateway, client, _ = self._gateway(httpx.MockTransport(handler))
        request = replace(
            _request("claude-opus-4-6"),
            tools=(
                LLMToolDefinition(
                    name="get_weather",
                    description="查询天气",
                    parameters={"type": "object", "properties": {}},
                ),
            ),
            messages=(
                LLMMessage(role="developer", content="应用约束"),
                LLMMessage(role="user", content="北京天气"),
                LLMMessage(
                    role="assistant",
                    tool_calls=(
                        LLMToolCall(
                            call_id="toolu_prior",
                            name="get_weather",
                            arguments_json='{"city":"上海"}',
                        ),
                    ),
                ),
                LLMMessage(
                    role="tool",
                    content='{"temperature":25}',
                    tool_call_id="toolu_prior",
                    tool_name="get_weather",
                ),
            ),
        )
        try:
            response = await gateway.complete(request)
        finally:
            await client.aclose()

        self.assertEqual(response.tool_calls[0].call_id, "toolu_1")
        self.assertEqual(payloads[0]["system"], "应用约束")
        self.assertEqual(payloads[0]["tool_choice"], {"type": "auto"})
        self.assertEqual(
            payloads[0]["messages"][-1]["content"][0]["type"],
            "tool_result",
        )

    async def test_empty_response_is_retried_and_recovers(self) -> None:
        request_count = 0

        def handler(_: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            text = "" if request_count == 1 else "重试后可用"
            return httpx.Response(
                200,
                json={
                    "id": f"deepseek-message-{request_count}",
                    "content": [{"type": "text", "text": text}],
                    "usage": {"input_tokens": 3, "output_tokens": 2},
                },
            )

        gateway, client, repository = self._gateway(
            httpx.MockTransport(handler),
            max_retries=1,
        )
        try:
            response = await gateway.complete(_request("deepseek-v4-pro"))
        finally:
            await client.aclose()
        self.assertEqual(response.text, "重试后可用")
        self.assertEqual(request_count, 2)
        records = await repository.list_calls(LLMUsageQuery())
        self.assertEqual(records.total, 1)
        self.assertEqual(records.items[0].status, "completed")

    async def test_invalid_json_response_is_retried_and_recovers(self) -> None:
        request_count = 0

        def handler(_: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            if request_count == 1:
                return httpx.Response(
                    200,
                    content=b"upstream returned invalid json",
                    headers={"content-type": "application/json"},
                )
            return httpx.Response(
                200,
                json={
                    "id": "deepseek-message-recovered",
                    "content": [{"type": "text", "text": "重试后可用"}],
                    "usage": {"input_tokens": 3, "output_tokens": 2},
                },
            )

        gateway, client, repository = self._gateway(
            httpx.MockTransport(handler),
            max_retries=1,
        )
        try:
            response = await gateway.complete(_request("deepseek-v4-pro"))
        finally:
            await client.aclose()

        self.assertEqual(response.text, "重试后可用")
        self.assertEqual(request_count, 2)
        records = await repository.list_calls(LLMUsageQuery())
        self.assertEqual(records.total, 1)
        self.assertEqual(records.items[0].status, "completed")

    async def test_repeated_empty_response_uses_retry_limit(self) -> None:
        request_count = 0

        def handler(_: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(
                200,
                json={
                    "id": f"deepseek-message-{request_count}",
                    "content": [],
                    "usage": {"input_tokens": 3, "output_tokens": 0},
                },
            )

        gateway, client, repository = self._gateway(
            httpx.MockTransport(handler),
            max_retries=1,
        )
        try:
            with self.assertRaises(LLMGatewayError) as context:
                await gateway.complete(_request("deepseek-v4-pro"))
        finally:
            await client.aclose()
        self.assertEqual(context.exception.code, "LLM_EMPTY_RESPONSE")
        self.assertEqual(request_count, 2)
        records = await repository.list_calls(LLMUsageQuery())
        self.assertEqual(records.total, 1)
        self.assertEqual(records.items[0].status, "failed")
        self.assertEqual(records.items[0].error_code, "LLM_EMPTY_RESPONSE")

    async def test_price_missing_is_unavailable_and_configured_price_is_estimated(
        self,
    ) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "output_text": "完成",
                    "usage": {"input_tokens": 1_000_000, "output_tokens": 2},
                },
            )

        gateway, client, _ = self._gateway(httpx.MockTransport(handler))
        try:
            unavailable = await gateway.complete(_request("gpt-5-6-luna"))
        finally:
            await client.aclose()
        self.assertIsNone(unavailable.cost.amount)
        self.assertEqual(unavailable.cost.kind, "unavailable")

        prices = json.dumps({"gpt-5-6-luna": {"input": "2", "output": "3"}})
        gateway, client, _ = self._gateway(
            httpx.MockTransport(handler), prices_json=prices
        )
        try:
            estimated = await gateway.complete(_request("gpt-5-6-luna"))
        finally:
            await client.aclose()
        self.assertEqual(estimated.cost.kind, "estimated")
        self.assertEqual(estimated.cost.amount, Decimal("2.000006"))

    async def test_failure_record_and_error_do_not_contain_secret_or_upstream_body(
        self,
    ) -> None:
        secret = "test-rightcode-key"

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                403,
                text=f"上游调试信息包含 {secret} 和内部堆栈",
            )

        gateway, client, repository = self._gateway(
            httpx.MockTransport(handler), key=secret
        )
        try:
            with self.assertRaises(LLMGatewayError) as context:
                await gateway.complete(_request("claude-opus-4-6"))
        finally:
            await client.aclose()
        self.assertNotIn(secret, str(context.exception))
        records = await repository.list_calls(LLMUsageQuery())
        self.assertEqual(records.total, 1)
        serialized = records.items[0].model_dump_json()
        self.assertNotIn(secret, serialized)
        self.assertNotIn("内部堆栈", serialized)

    def _gateway(
        self,
        transport: httpx.AsyncBaseTransport,
        *,
        prices_json: str = "{}",
        key: str = "test-rightcode-key",
        fallback_key: str = "",
        max_retries: int = 0,
        enforce_active_provider: bool = False,
    ) -> tuple[RightCodeLLMGateway, httpx.AsyncClient, JsonlLLMUsageRepository]:
        settings = _settings(
            self.assets_root,
            key=key,
            fallback_key=fallback_key,
            prices_json=prices_json,
            max_retries=max_retries,
        )
        repository = JsonlLLMUsageRepository(self.assets_root)
        client = httpx.AsyncClient(transport=transport)
        return (
            RightCodeLLMGateway(
                settings,
                LLMModelCatalog(settings),
                repository,
                client=client,
                replay_repository=JsonLLMCallReplayRepository(self.assets_root),
                enforce_active_provider=enforce_active_provider,
            ),
            client,
            repository,
        )


def _settings(
    assets_root: Path,
    *,
    key: str = "test-rightcode-key",
    fallback_key: str = "",
    prices_json: str = "{}",
    max_retries: int = 0,
) -> Settings:
    return Settings(
        project_assets_dir=assets_root,
        rightcode_api_key=SecretStr(key),
        deepseek_api_key=SecretStr(fallback_key),
        rightcode_model_prices_json=prices_json,
        rightcode_max_retries=max_retries,
    )


def _request(model_id: str) -> LLMRequest:
    return LLMRequest(
        model_id=model_id,
        messages=(
            LLMMessage(role="system", content="系统约束"),
            LLMMessage(role="user", content="用户输入"),
        ),
        task_type="test",
        task_name="网关测试",
    )
