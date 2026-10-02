"""模型目录、遥测与写作流式 API 集成测试。"""

import json
from collections.abc import AsyncIterator
from pathlib import Path
import tempfile
import unittest

import httpx
from httpx import ASGITransport, AsyncClient, MockTransport, Request, Response
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.messages import AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGenerationChunk
from pydantic import SecretStr

from taichu.api.deps import provide_llm_gateway
from taichu.application.contracts.llm import (
    LLMModelAvailability,
    LLMModelManagementPort,
    LLMModelProfile,
    LLMProviderId,
)
from taichu.application.models.llm_usage import LLMCallRecord
from taichu.application.services.import_service import ImportService
from taichu.config import Settings
from taichu.infrastructure.llm.catalog import LLMModelCatalog
from taichu.infrastructure.llm.rightcode import RightCodeLLMGateway
from taichu.infrastructure.storage.markdown_backend import ProjectAssetStorageBackend
from taichu.main import create_app
from tests.fakes import (
    InMemoryKnowledgeRepository,
    MVPNoRealLLMChatModel,
    make_test_llm_gateway,
)


class LLMApiTest(unittest.IsolatedAsyncioTestCase):
    async def test_model_management_accepts_a_provider_neutral_port(self) -> None:
        manager: LLMModelManagementPort = _ModelManagementFake()
        with tempfile.TemporaryDirectory() as temporary_directory:
            app = create_app(
                Settings(project_assets_dir=Path(temporary_directory)),
                knowledge_repository=InMemoryKnowledgeRepository(),
            )
            app.dependency_overrides[provide_llm_gateway] = lambda: manager
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                providers = await client.get("/api/llm/providers")
                models = await client.get("/api/llm/models")
                probe = await client.post("/api/llm/models/judge-model/probe")

        self.assertEqual(providers.status_code, 200)
        self.assertEqual(providers.json()["active_provider_id"], "rightcode")
        self.assertEqual(models.status_code, 200)
        self.assertEqual(models.json()["models"][0]["availability"], "available")
        self.assertEqual(probe.status_code, 200)
        self.assertEqual(probe.json()["actual_model_id"], "judge-model")

    def test_model_management_route_has_no_infrastructure_imports(self) -> None:
        repository_root = Path(__file__).resolve().parents[3]
        route_source = (
            repository_root / "src" / "taichu" / "api" / "routes" / "llm.py"
        ).read_text(encoding="utf-8")
        dependency_source = (
            repository_root / "src" / "taichu" / "api" / "deps.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("taichu.infrastructure.llm", route_source)
        self.assertNotIn("taichu.infrastructure.llm", dependency_source)
        self.assertNotIn("RightCodeLLMGateway", route_source)
        self.assertNotIn("LLMGatewayContract", route_source)

    async def test_switch_provider_limits_the_visible_model_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            app = create_app(
                Settings(
                    project_assets_dir=Path(temporary_directory),
                    rightcode_api_key=SecretStr("rightcode-key"),
                    deepseek_api_key=SecretStr("deepseek-key"),
                ),
                knowledge_repository=InMemoryKnowledgeRepository(),
            )
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                switched = await client.put(
                    "/api/llm/providers/active",
                    json={"provider_id": "deepseek_official"},
                )
                models = await client.get("/api/llm/models")
                preferences = (
                    await app.state.settings_preference_service.get_preferences()
                )

        self.assertEqual(switched.status_code, 200)
        self.assertEqual(switched.json()["active_provider_id"], "deepseek_official")
        self.assertEqual(models.status_code, 200)
        self.assertEqual(models.json()["default_model_id"], "deepseek-flash")
        self.assertEqual(len(models.json()["models"]), 2)
        self.assertEqual(
            {item["provider"] for item in models.json()["models"]},
            {"deepseek_official"},
        )
        self.assertEqual(
            {item["id"] for item in models.json()["models"]},
            {"deepseek-flash", "deepseek-v4-pro"},
        )
        self.assertEqual(preferences.llm_provider, "deepseek_official")

    async def test_catalog_is_safe_and_default_is_deepseek_v41_flash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            app = create_app(
                Settings(
                    project_assets_dir=Path(temporary_directory),
                    rightcode_api_key=SecretStr(""),
                    deepseek_api_key=SecretStr(""),
                ),
                knowledge_repository=InMemoryKnowledgeRepository(),
            )
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/llm/models")
                unknown = await client.post("/api/llm/models/unknown/probe")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["default_model_id"], "deepseek-flash")
        self.assertEqual(len(payload["models"]), 10)
        deepseek = next(
            item for item in payload["models"] if item["id"] == "deepseek-v4-pro"
        )
        claude = next(
            item for item in payload["models"] if item["id"] == "claude-opus-4-6"
        )
        self.assertTrue(deepseek["enabled"])
        self.assertEqual(deepseek["availability"], "unknown")
        self.assertTrue(claude["upstream_verified"])
        serialized = response.text.lower()
        self.assertNotIn("base_url", serialized)
        self.assertNotIn("base_url_key", serialized)
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("authorization", serialized)
        self.assertNotIn('"token":', serialized)
        self.assertEqual(unknown.status_code, 422)
        self.assertEqual(unknown.json()["error"]["code"], "LLM_MODEL_UNKNOWN")

    async def test_probe_response_exposes_requested_provider_without_fallback(
        self,
    ) -> None:
        requested_hosts: list[str] = []

        def upstream_handler(request: Request) -> Response:
            requested_hosts.append(request.url.host)
            if request.url.host == "rightapi.ai":
                raise httpx.ConnectError("RightCode 不可达", request=request)
            return Response(
                200,
                json={
                    "id": "official-must-not-be-used",
                    "content": [{"type": "text", "text": "官方可用"}],
                    "usage": {"input_tokens": 3, "output_tokens": 1},
                },
            )

        with tempfile.TemporaryDirectory() as temporary_directory:
            settings = Settings(
                project_assets_dir=Path(temporary_directory),
                rightcode_api_key=SecretStr("rightcode-key"),
                deepseek_api_key=SecretStr("deepseek-key"),
                rightcode_max_retries=0,
            )
            app = create_app(
                settings,
                knowledge_repository=InMemoryKnowledgeRepository(),
            )
            upstream = AsyncClient(transport=MockTransport(upstream_handler))
            app.state.llm_gateway = RightCodeLLMGateway(
                settings,
                LLMModelCatalog(settings),
                app.state.llm_usage_repository,
                client=upstream,
                replay_repository=app.state.llm_replay_repository,
            )
            try:
                async with AsyncClient(
                    transport=ASGITransport(app=app),
                    base_url="http://test",
                ) as client:
                    response = await client.post(
                        "/api/llm/models/deepseek-v4-pro/probe"
                    )
            finally:
                await upstream.aclose()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(requested_hosts, ["rightapi.ai"])
        self.assertEqual(
            response.json(),
            {
                "model_id": "deepseek-v4-pro",
                "availability": "unavailable",
                "last_probed_at": response.json()["last_probed_at"],
                "requested_provider": "rightcode",
                "requested_model_id": "deepseek-v4-pro",
                "actual_provider": "rightcode",
                "actual_model_id": "deepseek-v4-pro",
                "fallback_used": False,
                "fallback_from_provider": None,
                "wire_protocol": "anthropic_messages",
                "provider_request_id": None,
                "message": "模型检测失败：无法连接模型服务，请稍后重试。",
            },
        )

    async def test_usage_calls_detail_and_summary_do_not_return_prompts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            app = create_app(
                Settings(project_assets_dir=Path(temporary_directory)),
                knowledge_repository=InMemoryKnowledgeRepository(),
            )
            await app.state.llm_usage_repository.append(_record())
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                calls = await client.get(
                    "/api/llm/usage/calls?page=1&page_size=20&model_id=deepseek-v4-pro"
                )
                detail = await client.get("/api/llm/usage/calls/call-api-test")
                summary = await client.get("/api/llm/usage/summary")
                trend = await client.get("/api/llm/usage/trend?bucket=day")

        self.assertEqual(calls.json()["total"], 1)
        self.assertEqual(detail.json()["call_id"], "call-api-test")
        self.assertNotIn("prompt", detail.text.lower())
        self.assertEqual(summary.json()["total_calls"], 1)
        self.assertEqual(summary.json()["unavailable_cost_calls"], 1)
        self.assertEqual(trend.status_code, 200)
        self.assertEqual(trend.json()["bucket"], "day")
        self.assertEqual(trend.json()["points"][0]["total_tokens"], 15)

    async def test_writing_stream_emits_deltas_and_persists_same_output(self) -> None:
        output = json.dumps(
            {
                "output_type": "text_candidate",
                "text": "秦浩轩向山门走去。",
                "risk_notes": [],
                "used_evidence": [],
            },
            ensure_ascii=False,
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            assets_root = Path(temporary_directory)
            storage = ProjectAssetStorageBackend(assets_root)
            await ImportService(storage).import_text(
                "第一章 开始\n秦浩轩站在山门前。", source_name="stream.txt"
            )
            app = create_app(
                Settings(project_assets_dir=assets_root),
                llm_gateway=make_test_llm_gateway(
                    _StreamingStructuredChatModel(response_text=output)
                ),
                knowledge_repository=InMemoryKnowledgeRepository(),
            )
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/writing-ai/runs/stream",
                    json={
                        "button_type": "continue",
                        "chapter_id": "chapter_001",
                        "reference_scope": "chapter",
                        "model_id": "deepseek-v4-pro",
                    },
                )
                events = [json.loads(line) for line in response.text.splitlines()]
                completed = next(
                    item for item in events if item["type"] == "run_completed"
                )
                saved = await client.get(f"/api/writing-ai/runs/{completed['run_id']}")

        deltas = [item["delta"] for item in events if item["type"] == "text_delta"]
        self.assertEqual(deltas, ["秦浩", "轩向山门走去。"])
        self.assertEqual(json.loads(saved.json()["raw_llm_output"]), json.loads(output))


class _StreamingStructuredChatModel(MVPNoRealLLMChatModel):
    """按原生 tool-call 参数片段输出，用于验证应用层结构化增量。"""

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: object,
    ) -> AsyncIterator[ChatGenerationChunk]:
        del messages, stop, run_manager, kwargs
        assert self._bound_tool_name is not None
        split_at = self.response_text.index("秦浩") + len("秦浩")
        fragments = (self.response_text[:split_at], self.response_text[split_at:])
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {
                        "id": "call_streaming_structured_output",
                        "name": self._bound_tool_name,
                        "args": fragments[0],
                        "index": 0,
                        "type": "tool_call_chunk",
                    }
                ],
            )
        )
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {
                        "id": None,
                        "name": None,
                        "args": fragments[1],
                        "index": 0,
                        "type": "tool_call_chunk",
                    }
                ],
            )
        )


def _record() -> LLMCallRecord:
    return LLMCallRecord(
        call_id="call-api-test",
        run_id="run-api-test",
        task_type="writing_continue",
        task_name="续写",
        feature="写作 AI",
        chapter_ids=["chapter_001"],
        model_id="deepseek-v4-pro",
        model_display_name="DeepSeek V4 Pro",
        upstream_model="deepseek-v4-pro",
        wire_protocol="openai_responses",
        status="completed",
        started_at="2026-07-11T00:00:00Z",
        finished_at="2026-07-11T00:00:01Z",
        duration_ms=1000,
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
    )


class _ModelManagementFake:
    def __init__(self) -> None:
        self.active_provider: LLMProviderId = "rightcode"
        self._profile = LLMModelProfile(
            id="judge-model",
            display_name="中立测试模型",
            provider="rightcode",
            upstream_model="judge-model",
            wire_protocol="openai_responses",
            enabled=True,
            is_default=True,
            supports_streaming=True,
            upstream_verified=True,
        )

    def set_active_provider(self, provider: LLMProviderId) -> None:
        self.active_provider = provider

    def provider_configured(self, provider: LLMProviderId) -> bool:
        return provider == "rightcode"

    def provider_models(self, provider: LLMProviderId) -> list[LLMModelProfile]:
        return [self._profile] if provider == "rightcode" else []

    def list_models(self) -> list[LLMModelProfile]:
        return [self._profile]

    def availability_for(
        self, model_id: str, provider: LLMProviderId | None = None
    ) -> LLMModelAvailability:
        return LLMModelAvailability(
            availability="available",
            last_probed_at="2026-08-30T00:00:00Z",
            error=None,
        )

    async def probe_model(self, model_id: str) -> LLMModelAvailability:
        return LLMModelAvailability(
            availability="available",
            last_probed_at="2026-08-30T00:00:00Z",
            error=None,
            requested_provider="rightcode",
            requested_model_id=model_id,
            actual_provider="rightcode",
            actual_model_id=model_id,
            fallback_used=False,
            fallback_from_provider=None,
            wire_protocol="openai_responses",
            provider_request_id="probe-request",
        )
