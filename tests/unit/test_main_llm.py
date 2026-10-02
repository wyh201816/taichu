"""应用组合根的统一 LLM 网关测试。"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from pydantic import SecretStr

from taichu.application.contracts.llm import LLMModelIdentity
from taichu.config import Settings
from taichu.infrastructure.storage.markdown_backend import ProjectAssetStorageBackend
from taichu.main import create_app
from tests.fakes import (
    InMemoryGeneralAgentToolBudgetRepository,
    InMemoryKnowledgeRepository,
    MVPNoRealLLMChatModel,
    make_test_llm_gateway,
)


class MainLLMAssemblyTest(unittest.TestCase):
    def test_runtime_loads_persisted_global_provider_before_service_assembly(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            assets_root = Path(temporary_directory)
            storage = ProjectAssetStorageBackend(assets_root)
            asyncio.run(
                storage.write_preferences(
                    {
                        "font_size": 18,
                        "font_style": "serif",
                        "editor_background": "dark",
                        "llm_provider": "deepseek_official",
                        "updated_at": "2026-08-22T00:00:00Z",
                    }
                )
            )
            app = create_app(
                Settings(
                    project_assets_dir=assets_root,
                    rightcode_api_key=SecretStr("rightcode-key"),
                    deepseek_api_key=SecretStr("deepseek-key"),
                ),
                knowledge_repository=InMemoryKnowledgeRepository(),
                graph_checkpointer=InMemorySaver(),
                graph_store=InMemoryStore(),
                tool_budget_repository=InMemoryGeneralAgentToolBudgetRepository(),
            )

        self.assertEqual(app.state.llm_gateway.active_provider, "deepseek_official")
        self.assertEqual(app.state.llm_gateway.default_model_id, "deepseek-flash")
        self.assertEqual(len(app.state.llm_gateway.list_models()), 2)

    def test_default_runtime_uses_rightcode_catalog_without_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            app = create_app(
                Settings(
                    project_assets_dir=Path(temporary_directory),
                    rightcode_api_key=SecretStr(""),
                    deepseek_api_key=SecretStr(""),
                ),
                knowledge_repository=InMemoryKnowledgeRepository(),
                graph_checkpointer=InMemorySaver(),
                graph_store=InMemoryStore(),
                tool_budget_repository=InMemoryGeneralAgentToolBudgetRepository(),
            )

        self.assertEqual(len(app.state.llm_gateway.list_models()), 10)
        self.assertFalse(app.state.llm_gateway.configured)

    def test_injected_llm_is_reserved_for_tests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            app = create_app(
                Settings(project_assets_dir=Path(temporary_directory)),
                llm_gateway=make_test_llm_gateway(MVPNoRealLLMChatModel()),
                knowledge_repository=InMemoryKnowledgeRepository(),
                graph_checkpointer=InMemorySaver(),
                graph_store=InMemoryStore(),
                tool_budget_repository=InMemoryGeneralAgentToolBudgetRepository(),
            )

        identity = app.state.llm_gateway.model_identity
        self.assertFalse(identity.known)
        self.assertEqual(identity.unknown_reason, "注入模型未提供身份。")

    def test_injected_llm_uses_explicit_identity_when_provided(self) -> None:
        identity = LLMModelIdentity(
            provider="test",
            model_id="test-model",
            family="test-model",
            endpoint_kind="test",
            known=True,
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            app = create_app(
                Settings(project_assets_dir=Path(temporary_directory)),
                llm_gateway=make_test_llm_gateway(MVPNoRealLLMChatModel(), identity),
                knowledge_repository=InMemoryKnowledgeRepository(),
                graph_checkpointer=InMemorySaver(),
                graph_store=InMemoryStore(),
                tool_budget_repository=InMemoryGeneralAgentToolBudgetRepository(),
            )

        self.assertEqual(app.state.llm_gateway.model_identity, identity)

    def test_graph_persistence_components_must_be_injected_together(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaisesRegex(
                ValueError,
                "LangGraph Checkpointer、Store 和 Tool 调用预算仓储必须成组注入",
            ):
                create_app(
                    Settings(project_assets_dir=Path(temporary_directory)),
                    llm_gateway=make_test_llm_gateway(MVPNoRealLLMChatModel()),
                    knowledge_repository=InMemoryKnowledgeRepository(),
                    graph_checkpointer=InMemorySaver(),
                )


class MainResourceLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_startup_failure_closes_every_owned_runtime_resource(self) -> None:
        app, checkpoint_client = self._managed_app()
        evaluation_service = app.state.knowledge_extraction_evaluation_service
        runtime_service = app.state.general_agent_runtime_service
        extraction_service = app.state.knowledge_extraction_service
        vector_backend = app.state.vector_graph_backend
        evaluation_service.recover_interrupted = AsyncMock()
        runtime_service.recover_interrupted = AsyncMock(
            side_effect=RuntimeError("恢复失败")
        )
        extraction_service.recover_interrupted = AsyncMock()
        evaluation_service.start_watchdog = MagicMock()
        evaluation_service.shutdown = AsyncMock()
        runtime_service.shutdown = AsyncMock()
        extraction_service.shutdown = AsyncMock()
        vector_backend.close = AsyncMock()
        budget_repository = app.state.general_agent_tool_budget_repository
        budget_repository.aclose = AsyncMock(wraps=budget_repository.aclose)
        rightcode_client = app.state.llm_gateway._client

        with self.assertRaisesRegex(RuntimeError, "恢复失败"):
            async with app.router.lifespan_context(app):
                self.fail("启动恢复失败时不应进入服务阶段。")

        extraction_service.shutdown.assert_awaited_once_with()
        runtime_service.shutdown.assert_awaited_once_with()
        evaluation_service.shutdown.assert_awaited_once_with()
        vector_backend.close.assert_awaited_once_with()
        budget_repository.aclose.assert_awaited_once_with()
        checkpoint_client.close.assert_called_once_with()
        self.assertTrue(rightcode_client.is_closed)

    async def test_one_shutdown_failure_does_not_skip_other_cleanup(self) -> None:
        app, checkpoint_client = self._managed_app()
        evaluation_service = app.state.knowledge_extraction_evaluation_service
        runtime_service = app.state.general_agent_runtime_service
        extraction_service = app.state.knowledge_extraction_service
        vector_backend = app.state.vector_graph_backend
        evaluation_service.recover_interrupted = AsyncMock()
        runtime_service.recover_interrupted = AsyncMock()
        extraction_service.recover_interrupted = AsyncMock()
        evaluation_service.start_watchdog = MagicMock()
        evaluation_service.shutdown = AsyncMock()
        runtime_service.shutdown = AsyncMock()
        extraction_service.shutdown = AsyncMock(side_effect=RuntimeError("关闭失败"))
        vector_backend.close = AsyncMock()
        budget_repository = app.state.general_agent_tool_budget_repository
        budget_repository.aclose = AsyncMock(wraps=budget_repository.aclose)
        rightcode_client = app.state.llm_gateway._client

        with self.assertRaisesRegex(RuntimeError, "关闭失败"):
            async with app.router.lifespan_context(app):
                pass

        extraction_service.shutdown.assert_awaited_once_with()
        runtime_service.shutdown.assert_awaited_once_with()
        evaluation_service.shutdown.assert_awaited_once_with()
        vector_backend.close.assert_awaited_once_with()
        budget_repository.aclose.assert_awaited_once_with()
        checkpoint_client.close.assert_called_once_with()
        self.assertTrue(rightcode_client.is_closed)

    def _managed_app(self):
        self.addCleanup(self._temporary_directory.cleanup)
        checkpoint_client = MagicMock()
        with patch("taichu.main.MongoClient", return_value=checkpoint_client):
            app = create_app(
                Settings(
                    project_assets_dir=Path(self._temporary_directory.name)
                ),
                knowledge_repository=InMemoryKnowledgeRepository(),
            )
        return app, checkpoint_client

    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
