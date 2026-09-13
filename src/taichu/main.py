"""组装并启动太初 FastAPI 应用。"""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
import json
import uvicorn
from typing import Any, cast
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.mongodb import MongoDBSaver
from langgraph.store.base import BaseStore
from langgraph.store.mongodb import MongoDBStore
from pymongo import MongoClient

from taichu.api.router import register_routes
from taichu.application.agents.registry import AgentRegistry
from taichu.application.subagents.registry import SubagentRegistry
from taichu.application.capabilities import CapabilityContext
from taichu.application.general_agent.events import GeneralAgentEventCenter
from taichu.application.general_agent.context import (
    ContextAssembler,
)
from taichu.application.general_agent.executor import DynamicDagExecutor
from taichu.application.general_agent.memory_policy import AgentMemoryPolicy
from taichu.application.general_agent.orchestrator import OrchestratorAgent
from taichu.application.general_agent.service import GeneralAgentRuntimeService
from taichu.application.evaluations.general_agent_benchmark.container import (
    build_general_agent_benchmark_services,
)
from taichu.application.evaluations.general_agent_benchmark.issue_correlations import (
    IssueCorrelationRepository,
)
from taichu.application.evaluations.general_agent_benchmark.observability import (
    UnavailableBenchmarkObservabilityQuery,
)
from taichu.application.evaluations.general_agent_benchmark.portfolio import (
    build_benchmark_portfolio,
)
from taichu.application.evaluations.general_agent_benchmark.services import (
    BenchmarkCatalogEntry,
)
from taichu.application.evaluations.general_agent_benchmark.suite_loader import (
    load_authored_suite,
)
from taichu.application.contracts.llm import LLMModelManagementError
from taichu.infrastructure.llm.contracts import LLMGatewayContract
from taichu.application.contracts.knowledge_repository import (
    KnowledgeRepositoryUnavailableError,
    StructuredKnowledgeRepository,
)
from taichu.application.contracts.knowledge_sedimentation_progress_repository import (
    InMemoryKnowledgeSedimentationProgressRepository,
)
from taichu.application.contracts.general_agent_tool_budget import (
    GeneralAgentToolBudgetRepository,
)
from taichu.application.services.ai_card_service import AICardService
from taichu.application.services.agent_memory_evidence_service import (
    AgentMemoryEvidenceService,
)
from taichu.application.services.agent_memory_service import AgentMemoryService
from taichu.application.services.chapter_summary_service import (
    ChapterSummaryService,
)
from taichu.application.services.chapter_service import ChapterService
from taichu.application.services.export_service import ExportService
from taichu.application.services.inbox_service import InboxService
from taichu.application.services.knowledge_service import (
    KnowledgeService,
    KnowledgeUnavailableError,
)
from taichu.application.services.knowledge_extraction_service import (
    KnowledgeExtractionService,
)
from taichu.application.services.knowledge_extraction_evaluation_service import (
    KnowledgeExtractionEvaluationService,
)
from taichu.application.services.agent_task_event_service import AgentTaskEventCenter
from taichu.application.services.mvp_inbox_service import MVPInboxService
from taichu.application.services.outline_service import OutlineService
from taichu.application.services.selection_ai_service import SelectionAIService
from taichu.application.services.settings_service import SettingsPreferenceService
from taichu.application.services.writing_ai_service import WritingAIService
from taichu.application.vector_graph import VectorGraphRAGService
from taichu.application.services.invocation_policy_service import (
    InvocationPolicyService,
)
from taichu.application.services.model_role_router import ModelRoleRouter
from taichu.application.external_research.service import ExternalResearchService
from taichu.application.tools.registry import ToolRegistry
from taichu.config import Settings, settings
from taichu.domain.models import EditorPreferences
from taichu.infrastructure.llm.adapter import GatewayChatModel
from taichu.infrastructure.llm.catalog import LLMModelCatalog
from taichu.infrastructure.llm.rightcode import RightCodeLLMGateway
from taichu.infrastructure.llm_usage import JsonlLLMUsageRepository
from taichu.infrastructure.long_term_memory import MarkdownLongTermMemoryRetriever
from taichu.infrastructure.llm_replays import JsonLLMCallReplayRepository
from taichu.infrastructure.evaluations import (
    JsonEvaluationDatasetRepository,
    JsonEvaluationResultStore,
    create_evaluation_judge,
)
from taichu.infrastructure.evaluations.general_agent_benchmark.runtime_factory import (
    production_capability_catalog_snapshot,
)
from taichu.infrastructure.evaluations.general_agent_benchmark.artifact_hydration import (
    load_frozen_benchmark_query_snapshot,
)
from taichu.infrastructure.evaluations.general_agent_benchmark.artifact_repository import (
    GeneralAgentBenchmarkArtifactRepository,
)
from taichu.infrastructure.evaluations.general_agent_benchmark.interactive_synthetic import (
    InteractiveSyntheticExecution,
)
from taichu.infrastructure.evaluations.general_agent_benchmark.persistent_runtime import (
    JsonBenchmarkRunResourceService,
    JsonSuiteRunStore,
)
from taichu.infrastructure.evaluations.general_agent_benchmark.opik_integration import (
    configure_opik_observability,
)
from taichu.infrastructure.evaluations.general_agent_benchmark.opik_query import (
    OpikBenchmarkObservabilityQuery,
)
from taichu.infrastructure.evaluations.rag.result_repository import (
    RAGEvaluationResultRepository,
)
from taichu.infrastructure.plugin_discovery import (
    discover_agents,
    discover_subagents,
    discover_tools,
)
from taichu.infrastructure.agent_runs import JsonAgentRunStore
from taichu.infrastructure.knowledge import (
    MongoKnowledgeRepository,
    MongoKnowledgeSedimentationProgressRepository,
)
from taichu.infrastructure.vector_graph import (
    BGEReranker,
    HybridVectorGraphBackend,
    MilvusVectorGraphBackend,
)
from taichu.infrastructure.storage.json_backend import JsonStorageBackend
from taichu.infrastructure.storage.markdown_backend import (
    ProjectAssetStorageBackend,
)
from taichu.infrastructure.external_research import (
    DuckDuckGoExternalResearchBackend,
)
from taichu.infrastructure.invocations import JsonlInvocationTraceRepository
from taichu.infrastructure.artifacts import JsonIntermediateArtifactRepository
from taichu.infrastructure.general_agent_runs import (
    LangGraphGeneralAgentCapabilityResultRepository,
    JsonGeneralAgentContextSnapshotRepository,
    JsonGeneralAgentEffectRepository,
    JsonGeneralAgentRunRepository,
    MongoGeneralAgentToolBudgetRepository,
)
from taichu.infrastructure.agent_memory import (
    LangGraphAgentMemoryRepository,
)

_FIXED_GENERAL_AGENT_BENCHMARK_SUITE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "evaluations"
    / "general_writing_agent_benchmark"
    / "suite.json"
)
_FIXED_GENERAL_AGENT_BENCHMARK_FIXTURE = (
    _FIXED_GENERAL_AGENT_BENCHMARK_SUITE.parent / "fixtures" / "core_novel"
)
_FIXED_GENERAL_AGENT_BENCHMARK_CLAIMS = (
    _FIXED_GENERAL_AGENT_BENCHMARK_SUITE.parent / "claim-catalog.json"
)


def create_app(
    app_settings: Settings = settings,
    *,
    llm_gateway: LLMGatewayContract | None = None,
    knowledge_repository: StructuredKnowledgeRepository | None = None,
    graph_checkpointer: BaseCheckpointSaver[Any] | None = None,
    graph_store: BaseStore | None = None,
    tool_budget_repository: GeneralAgentToolBudgetRepository | None = None,
) -> FastAPI:
    """创建并组装 FastAPI 应用。"""

    configure_opik_observability(
        enabled=app_settings.opik_enabled,
        project_name=app_settings.opik_project_name,
        url_override=app_settings.opik_url_override,
        api_key=app_settings.opik_api_key.get_secret_value(),
        workspace=app_settings.opik_workspace,
    )
    persistence_components = (
        graph_checkpointer,
        graph_store,
        tool_budget_repository,
    )
    supplied_persistence_count = sum(
        component is not None for component in persistence_components
    )
    if supplied_persistence_count not in {0, len(persistence_components)}:
        raise ValueError(
            "LangGraph Checkpointer、Store 和 Tool 调用预算仓储必须成组注入。"
        )
    managed_tool_budget_repository = tool_budget_repository is None
    storage = JsonStorageBackend(app_settings.project_assets_dir / "source")
    project_storage = ProjectAssetStorageBackend(app_settings.project_assets_dir)
    chapter_service = ChapterService(project_storage)
    outline_service = OutlineService(project_storage)
    settings_preference_service = SettingsPreferenceService(project_storage)
    model_catalog = LLMModelCatalog(app_settings)
    llm_usage_repository = JsonlLLMUsageRepository(app_settings.project_assets_dir)
    llm_replay_repository = JsonLLMCallReplayRepository(app_settings.project_assets_dir)
    if llm_gateway is not None:
        llm_service = llm_gateway
        llm_configured = True
    else:
        rightcode_gateway = RightCodeLLMGateway(
            app_settings,
            model_catalog,
            llm_usage_repository,
            replay_repository=llm_replay_repository,
            enforce_active_provider=True,
        )
        initial_preferences = EditorPreferences.model_validate(
            project_storage.read_preferences_snapshot()
        )
        try:
            rightcode_gateway.set_active_provider(
                initial_preferences.llm_provider.value
            )
        except LLMModelManagementError:
            # 已保存供应商失去密钥时保持可启动，由供应商页面提示重新配置。
            pass
        llm_service = rightcode_gateway
        llm_configured = rightcode_gateway.configured
    active_default_model = next(
        (profile.id for profile in llm_service.list_models() if profile.is_default),
        app_settings.rightcode_default_model_id,
    )
    application_chat_model = GatewayChatModel(
        llm_service,
        model_id=active_default_model,
    )
    ai_card_service = AICardService(project_storage)
    inbox_service = InboxService(project_storage, ai_card_service)
    managed_knowledge_repository = knowledge_repository is None
    if knowledge_repository is None:
        knowledge_repository = MongoKnowledgeRepository(
            app_settings.mongodb_uri,
            app_settings.mongodb_database,
        )
    knowledge_service = KnowledgeService(knowledge_repository)
    milvus_vector_graph_backend = MilvusVectorGraphBackend(
        milvus_uri=app_settings.milvus_uri,
        milvus_token=app_settings.milvus_token.get_secret_value(),
        collection_prefix=app_settings.milvus_collection_prefix,
        llm=application_chat_model,
        llm_model=(app_settings.vector_graph_llm_model or active_default_model),
        embedding_base_url=app_settings.embedding_base_url,
        embedding_model=app_settings.embedding_model_id,
        embedding_dimensions=app_settings.embedding_dimensions,
        manifest_path=(
            app_settings.project_assets_dir
            / "generated"
            / "milvus_vector_graph"
            / "active_manifest.json"
        ),
        hnsw_m=app_settings.milvus_hnsw_m,
        hnsw_ef_construction=app_settings.milvus_hnsw_ef_construction,
        hnsw_ef_search=app_settings.milvus_hnsw_ef_search,
        rrf_k=app_settings.milvus_rrf_k,
        expansion_max_seed_entities=(
            app_settings.vector_graph_expansion_max_seed_entities
        ),
        expansion_max_seed_relations=(
            app_settings.vector_graph_expansion_max_seed_relations
        ),
        expansion_max_hop=app_settings.vector_graph_expansion_max_hop,
        expansion_max_entities_per_hop=(
            app_settings.vector_graph_expansion_max_entities_per_hop
        ),
        expansion_relations_per_entity=(
            app_settings.vector_graph_expansion_relations_per_entity
        ),
        expansion_candidate_pool_multiplier=(
            app_settings.vector_graph_expansion_candidate_pool_multiplier
        ),
        expansion_hub_relations_per_entity=(
            app_settings.vector_graph_expansion_hub_relations_per_entity
        ),
        expansion_hub_degree_threshold=(
            app_settings.vector_graph_expansion_hub_degree_threshold
        ),
        expansion_beam_width=app_settings.vector_graph_expansion_beam_width,
        expansion_max_total_relations=(
            app_settings.vector_graph_expansion_max_total_relations
        ),
        expansion_max_graph_passages=(
            app_settings.vector_graph_expansion_max_graph_passages
        ),
        final_top_k=app_settings.vector_graph_passage_top_k,
    )
    vector_graph_backend = HybridVectorGraphBackend(
        milvus=milvus_vector_graph_backend,
        reranker=BGEReranker(
            base_url=app_settings.reranker_base_url,
            model_id=app_settings.reranker_model_id,
            timeout_seconds=app_settings.reranker_request_timeout_seconds,
        ),
        candidate_top_k=app_settings.vector_graph_passage_top_k,
        final_top_k=app_settings.vector_graph_reranker_top_k,
    )
    vector_graph_rag_service = VectorGraphRAGService(
        chapter_service=chapter_service,
        knowledge_repository=knowledge_repository,
        backend=vector_graph_backend,
        manuscript_chunk_size=app_settings.vector_graph_manuscript_chunk_size,
        manuscript_chunk_overlap=(app_settings.vector_graph_manuscript_chunk_overlap),
    )
    invocation_trace_repository = JsonlInvocationTraceRepository(
        app_settings.project_assets_dir
    )
    artifact_repository = JsonIntermediateArtifactRepository(
        app_settings.project_assets_dir
    )
    general_agent_run_repository = JsonGeneralAgentRunRepository(
        app_settings.project_assets_dir
    )
    general_agent_checkpoint_client: MongoClient[Any] | None = None
    if supplied_persistence_count == 0:
        general_agent_checkpoint_client = MongoClient(
            app_settings.mongodb_uri,
            tz_aware=True,
            serverSelectionTimeoutMS=5_000,
        )
        graph_checkpointer = MongoDBSaver(
            general_agent_checkpoint_client,
            db_name=app_settings.mongodb_database,
            checkpoint_collection_name="langgraph_checkpoints",
            writes_collection_name="langgraph_checkpoint_writes",
        )
        graph_store = MongoDBStore(
            general_agent_checkpoint_client[app_settings.mongodb_database][
                "langgraph_store"
            ]
        )
        tool_budget_repository = MongoGeneralAgentToolBudgetRepository(
            app_settings.mongodb_uri,
            app_settings.mongodb_database,
        )
    assert graph_checkpointer is not None
    assert graph_store is not None
    assert tool_budget_repository is not None
    general_agent_graph_checkpointer = graph_checkpointer
    general_agent_graph_store = graph_store
    general_agent_effect_repository = JsonGeneralAgentEffectRepository(
        app_settings.project_assets_dir
    )
    general_agent_capability_result_repository = (
        LangGraphGeneralAgentCapabilityResultRepository(general_agent_graph_store)
    )
    general_agent_context_snapshot_repository = (
        JsonGeneralAgentContextSnapshotRepository(app_settings.project_assets_dir)
    )
    agent_memory_repository = LangGraphAgentMemoryRepository(general_agent_graph_store)
    agent_memory_service = AgentMemoryService(
        repository=agent_memory_repository,
        evidence_resolver=AgentMemoryEvidenceService(
            chapter_service=chapter_service,
            knowledge_service=knowledge_service,
            artifact_repository=artifact_repository,
            project_storage=project_storage,
        ),
        policy=AgentMemoryPolicy(
            age_decay_days=app_settings.general_agent_memory_age_decay_days,
            minimum_relevance=(app_settings.general_agent_memory_minimum_relevance),
        ),
    )
    general_agent_context_assembler = ContextAssembler(
        memory_service=agent_memory_service,
        long_term_memory_retriever=MarkdownLongTermMemoryRetriever(
            app_settings.project_assets_dir
            / "source"
            / "workspace"
            / "long_term_memory.md"
        ),
    )
    general_agent_event_center = GeneralAgentEventCenter()
    invocation_policy_service = InvocationPolicyService()
    external_research_service = ExternalResearchService(
        DuckDuckGoExternalResearchBackend()
    )
    model_role_router = ModelRoleRouter.from_json(
        active_default_model,
        app_settings.agent_model_roles_json,
    )
    sedimentation_progress_repository = (
        MongoKnowledgeSedimentationProgressRepository(
            app_settings.mongodb_uri,
            app_settings.mongodb_database,
        )
        if managed_knowledge_repository
        else InMemoryKnowledgeSedimentationProgressRepository()
    )
    mvp_inbox_service = MVPInboxService(project_storage, knowledge_service)
    knowledge_run_store = JsonAgentRunStore(app_settings.project_assets_dir)
    agent_task_events = AgentTaskEventCenter()
    knowledge_extraction_service = KnowledgeExtractionService(
        chapter_service=chapter_service,
        llm=application_chat_model,
        model_catalog=llm_service,
        knowledge_repository=knowledge_repository,
        knowledge_service=knowledge_service,
        run_store=knowledge_run_store,
        sedimentation_progress_repository=sedimentation_progress_repository,
        task_events=agent_task_events,
        default_model_id=active_default_model,
    )
    evaluation_dataset_repository = JsonEvaluationDatasetRepository(
        app_settings.evaluation_datasets_dir,
        app_settings.project_assets_dir / "source",
    )
    evaluation_result_repository = JsonEvaluationResultStore(
        app_settings.project_assets_dir
    )
    rag_evaluation_result_repository = RAGEvaluationResultRepository(
        app_settings.project_assets_dir / "derived" / "rag_evaluations"
    )
    evaluation_judge = create_evaluation_judge(
        app_settings,
        application_chat_model,
        llm_service,
        configured=llm_configured,
    )
    knowledge_extraction_evaluation_service = KnowledgeExtractionEvaluationService(
        dataset_repository=evaluation_dataset_repository,
        result_repository=evaluation_result_repository,
        run_store=knowledge_run_store,
        chapter_service=chapter_service,
        judge=evaluation_judge,
    )
    writing_ai_service = WritingAIService(
        storage=project_storage,
        chapter_service=chapter_service,
        retrieval_service=vector_graph_rag_service,
        llm=application_chat_model,
        model_catalog=llm_service,
        default_model_id=active_default_model,
        llm_configured=llm_configured,
    )
    selection_ai_service = SelectionAIService(
        application_chat_model,
        ai_card_service,
        default_model_id=active_default_model,
    )
    export_service = ExportService(project_storage, knowledge_repository)
    chapter_summary_service = ChapterSummaryService(
        storage=project_storage,
        chapter_service=chapter_service,
        knowledge_repository=knowledge_repository,
        llm=application_chat_model,
        ai_card_service=ai_card_service,
        default_model_id=active_default_model,
    )
    general_agent_chat_model = application_chat_model.for_request(
        model_id=model_role_router.model_for("orchestrator"),
    )
    from taichu.infrastructure.general_agent_runs.result_files import (
        JsonContextResultStore,
    )
    from taichu.infrastructure.llm.context_tokens import ModelContextTokens
    from taichu.application.general_agent.pipeline import ContextPipeline
    from taichu.application.general_agent.pipeline_models import ContextPipelinePolicy

    context_result_store = JsonContextResultStore(app_settings.project_assets_dir)
    context_token_counter = ModelContextTokens(
        windows=json.loads(app_settings.general_agent_model_windows_json),
        tokenizer_paths=json.loads(app_settings.general_agent_tokenizers_json),
    )
    context_pipeline = ContextPipeline(
        model=general_agent_chat_model,
        counter=context_token_counter,
        result_store=context_result_store,
        store=general_agent_graph_store,
        policy=ContextPipelinePolicy(
            result_preview_tokens=app_settings.general_agent_result_preview_tokens,
            extractor_model_id=model_role_router.model_for("context_extractor"),
        ),
        trace_repository=invocation_trace_repository,
        memory_service=agent_memory_service,
    )
    capability_context = CapabilityContext(
        capabilities={
            "llm": application_chat_model,
            "chapter_service": chapter_service,
            "outline_service": outline_service,
            "knowledge_service": knowledge_service,
            "knowledge_repository": knowledge_repository,
            "vector_graph_rag_service": vector_graph_rag_service,
            "external_research_service": external_research_service,
            "invocation_policy_service": invocation_policy_service,
            "invocation_trace_repository": invocation_trace_repository,
            "artifact_repository": artifact_repository,
            "model_role_router": model_role_router,
            "knowledge_run_store": knowledge_run_store,
            "context_result_store": context_result_store,
            "general_agent_run_repository": general_agent_run_repository,
            "storage": storage,
            "graph_store": general_agent_graph_store,
        }
    )
    agent_registry = AgentRegistry(capability_context)
    agent_registry.register_all(discover_agents("taichu.application.agents"))
    tool_registry = ToolRegistry(
        capability_context,
        invocation_trace_repository,
        tool_budget_repository=tool_budget_repository,
        require_tool_budget=True,
    )
    discovered_tools = discover_tools("taichu.application.tools")
    tool_registry.register_all(discovered_tools)
    subagent_context = CapabilityContext(
        capabilities={
            **capability_context.capabilities,
            "llm": general_agent_chat_model,
            "tool_registry": tool_registry,
        }
    )
    subagent_registry = SubagentRegistry(
        subagent_context,
        invocation_trace_repository,
    )
    discovered_subagents = discover_subagents("taichu.application.subagents")
    subagent_registry.register_all(discovered_subagents)
    capability_handler_identities = {
        **{
            ("tool", plugin.manifest.name): (
                f"{plugin.run.__module__}:{plugin.run.__qualname__}"
            )
            for plugin in discovered_tools
        },
        **{
            ("subagent", plugin.manifest.name): (
                f"{plugin.run.__module__}:{plugin.run.__qualname__}"
            )
            for plugin in discovered_subagents
        },
    }
    orchestrator_agent = OrchestratorAgent(
        llm=general_agent_chat_model,
        model_router=model_role_router,
        tool_registry=tool_registry,
        subagent_registry=subagent_registry,
        trace_repository=invocation_trace_repository,
        capability_retrieval_limit=(
            app_settings.general_agent_capability_retrieval_limit
        ),
    )
    dynamic_dag_executor = DynamicDagExecutor(
        tool_registry=tool_registry,
        subagent_registry=subagent_registry,
        policy_service=invocation_policy_service,
        capability_result_repository=(general_agent_capability_result_repository),
        capability_handler_identities=capability_handler_identities,
        effect_repository=general_agent_effect_repository,
    )
    general_agent_runtime_service = GeneralAgentRuntimeService(
        repository=general_agent_run_repository,
        event_center=general_agent_event_center,
        orchestrator=orchestrator_agent,
        executor=dynamic_dag_executor,
        policy_service=invocation_policy_service,
        memory_service=agent_memory_service,
        context_assembler=general_agent_context_assembler,
        context_pipeline=context_pipeline,
        capability_result_repository=(general_agent_capability_result_repository),
        graph_checkpointer=general_agent_graph_checkpointer,
        graph_store=general_agent_graph_store,
        effect_repository=general_agent_effect_repository,
        context_snapshot_repository=general_agent_context_snapshot_repository,
        llm_replay_repository=llm_replay_repository,
        tool_budget_repository=tool_budget_repository,
    )
    benchmark_capability_catalog = production_capability_catalog_snapshot()
    benchmark_suite = load_authored_suite(
        _FIXED_GENERAL_AGENT_BENCHMARK_SUITE,
        expected_capability_catalog_hash=(benchmark_capability_catalog.canonical_hash),
    )
    interactive_benchmark_execution = InteractiveSyntheticExecution(
        suite=benchmark_suite,
        fixture_root=_FIXED_GENERAL_AGENT_BENCHMARK_FIXTURE,
        claim_catalog_path=_FIXED_GENERAL_AGENT_BENCHMARK_CLAIMS,
        workspaces_root=(
            app_settings.project_assets_dir
            / "derived"
            / "general_agent_benchmarks"
            / "interactive-workspaces"
        ),
        mongodb_uri=app_settings.mongodb_uri,
        capability_catalog=benchmark_capability_catalog,
    )
    benchmark_runtime_root = (
        app_settings.project_assets_dir
        / "derived"
        / "general_agent_benchmarks"
        / "interactive-runtime"
    )
    benchmark_portfolio = build_benchmark_portfolio(benchmark_suite)
    benchmark_observability = (
        OpikBenchmarkObservabilityQuery.create(
            suite=benchmark_suite,
            entries=benchmark_portfolio,
            project_name=app_settings.opik_project_name,
            workspace=app_settings.opik_workspace,
            api_key=app_settings.opik_api_key.get_secret_value(),
            url_override=app_settings.opik_url_override,
        )
        if app_settings.opik_enabled
        else UnavailableBenchmarkObservabilityQuery(
            project_name=app_settings.opik_project_name,
            suite_content_hash=benchmark_suite.content_hash,
        )
    )
    general_agent_benchmark_services = build_general_agent_benchmark_services(
        catalog_entries=(BenchmarkCatalogEntry.from_suite(benchmark_suite),),
        authored_suites=(benchmark_suite,),
        suite_run_store=JsonSuiteRunStore(benchmark_runtime_root / "runs"),
        execute_case=interactive_benchmark_execution.execute_case,
        finalize_suite=interactive_benchmark_execution.finalize,
        issue_correlation_repository=IssueCorrelationRepository(),
        query_hydration=load_frozen_benchmark_query_snapshot(
            GeneralAgentBenchmarkArtifactRepository(
                app_settings.project_assets_dir / "derived" / "general_agent_benchmarks"
            )
        ),
        resources=JsonBenchmarkRunResourceService(benchmark_runtime_root / "artifacts"),
        observability=benchmark_observability,
    )
    interactive_benchmark_execution.bind_resources(
        general_agent_benchmark_services.resources
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as resources:
            if managed_knowledge_repository:
                resources.push_async_callback(
                    cast(MongoKnowledgeRepository, knowledge_repository).close
                )
                resources.push_async_callback(
                    cast(
                        MongoKnowledgeSedimentationProgressRepository,
                        sedimentation_progress_repository,
                    ).close
                )
            if general_agent_checkpoint_client is not None:
                resources.callback(general_agent_checkpoint_client.close)
            if managed_tool_budget_repository:
                resources.push_async_callback(
                    cast(
                        MongoGeneralAgentToolBudgetRepository,
                        tool_budget_repository,
                    ).aclose
                )
            if isinstance(llm_service, RightCodeLLMGateway):
                resources.push_async_callback(llm_service.aclose)
            resources.push_async_callback(vector_graph_backend.close)
            resources.push_async_callback(
                knowledge_extraction_evaluation_service.shutdown
            )
            resources.push_async_callback(general_agent_runtime_service.shutdown)
            resources.push_async_callback(knowledge_extraction_service.shutdown)

            if isinstance(llm_service, RightCodeLLMGateway):
                preferences = await settings_preference_service.get_preferences()
                try:
                    llm_service.set_active_provider(preferences.llm_provider.value)
                except LLMModelManagementError:
                    # 已保存供应商失去密钥时保持可启动，由供应商页面提示重新配置。
                    pass
            if managed_knowledge_repository:
                try:
                    await cast(
                        MongoKnowledgeRepository,
                        knowledge_repository,
                    ).initialize()
                    await cast(
                        MongoKnowledgeSedimentationProgressRepository,
                        sedimentation_progress_repository,
                    ).initialize()
                except Exception as error:
                    raise RuntimeError(
                        f"MongoDB 知识库初始化失败，后端已停止启动：{error}"
                    ) from error
            await knowledge_extraction_evaluation_service.recover_interrupted()
            await general_agent_runtime_service.recover_interrupted()
            await knowledge_extraction_service.recover_interrupted()
            knowledge_extraction_evaluation_service.start_watchdog()
            yield

    application = FastAPI(
        title="Taichu",
        description="太初 - 单本玄幻小说个人写作助手",
        lifespan=lifespan,
    )

    @application.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "正常"}

    application.state.agent_registry = agent_registry
    application.state.tool_registry = tool_registry
    application.state.subagent_registry = subagent_registry
    application.state.general_agent_run_repository = general_agent_run_repository
    application.state.general_agent_event_center = general_agent_event_center
    application.state.general_agent_runtime_service = general_agent_runtime_service
    application.state.general_agent_context_snapshot_repository = (
        general_agent_context_snapshot_repository
    )
    application.state.general_agent_capability_result_repository = (
        general_agent_capability_result_repository
    )
    application.state.agent_memory_service = agent_memory_service
    application.state.agent_memory_repository = agent_memory_repository
    application.state.general_agent_graph_store = general_agent_graph_store
    application.state.general_agent_tool_budget_repository = tool_budget_repository
    application.state.general_agent_benchmark_services = (
        general_agent_benchmark_services
    )
    application.state.invocation_policy_service = invocation_policy_service
    application.state.invocation_trace_repository = invocation_trace_repository
    application.state.artifact_repository = artifact_repository
    application.state.external_research_service = external_research_service
    application.state.app_settings = app_settings
    application.state.model_role_router = model_role_router
    application.state.storage = storage
    application.state.project_storage = project_storage
    application.state.chapter_service = chapter_service
    application.state.outline_service = outline_service
    application.state.ai_card_service = ai_card_service
    application.state.inbox_service = inbox_service
    application.state.mvp_inbox_service = mvp_inbox_service
    application.state.export_service = export_service
    application.state.knowledge_service = knowledge_service
    application.state.knowledge_repository = knowledge_repository
    application.state.vector_graph_backend = vector_graph_backend
    application.state.vector_graph_rag_service = vector_graph_rag_service
    application.state.sedimentation_progress_repository = (
        sedimentation_progress_repository
    )
    application.state.knowledge_run_store = knowledge_run_store
    application.state.agent_task_events = agent_task_events
    application.state.knowledge_extraction_service = knowledge_extraction_service
    application.state.knowledge_extraction_evaluation_service = (
        knowledge_extraction_evaluation_service
    )
    application.state.evaluation_dataset_repository = evaluation_dataset_repository
    application.state.evaluation_result_repository = evaluation_result_repository
    application.state.rag_evaluation_result_repository = (
        rag_evaluation_result_repository
    )
    application.state.evaluation_judge = evaluation_judge
    application.state.selection_ai_service = selection_ai_service
    application.state.chapter_summary_service = chapter_summary_service
    application.state.settings_preference_service = settings_preference_service
    application.state.writing_ai_service = writing_ai_service
    application.state.llm_gateway = llm_service
    application.state.chat_model = application_chat_model
    application.state.llm_model_catalog = model_catalog
    application.state.llm_usage_repository = llm_usage_repository
    application.state.llm_replay_repository = llm_replay_repository
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_routes(application)
    application.add_exception_handler(HTTPException, _http_exception_handler)
    application.add_exception_handler(
        RequestValidationError,
        _validation_exception_handler,
    )
    application.add_exception_handler(
        KnowledgeUnavailableError,
        _knowledge_unavailable_exception_handler,
    )
    application.add_exception_handler(
        KnowledgeRepositoryUnavailableError,
        _knowledge_unavailable_exception_handler,
    )
    return application


async def _http_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    http_error = cast(HTTPException, exc)
    if isinstance(http_error.detail, dict) and "error" in http_error.detail:
        return JSONResponse(
            status_code=http_error.status_code,
            content=http_error.detail,
        )
    return JSONResponse(
        status_code=http_error.status_code,
        content={"detail": http_error.detail},
    )


async def _validation_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    cast(RequestValidationError, exc)
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "请求内容不完整或格式不正确，请检查后再试。",
            }
        },
    )


async def _knowledge_unavailable_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    message = str(exc).strip() or "MongoDB 知识库暂时不可用，请稍后重试。"
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "code": "KNOWLEDGE_UNAVAILABLE",
                "message": message,
            }
        },
    )


app = create_app()


def main() -> None:
    """启动开发服务器。"""
    reload_directory = str(Path(__file__).resolve().parent)
    uvicorn.run(
        "taichu.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.backend_reload,
        reload_dirs=[reload_directory] if settings.backend_reload else None,
        # 前端事件流是长连接，开发热重载不能无限等待它自行结束。
        timeout_graceful_shutdown=5 if settings.backend_reload else None,
    )


if __name__ == "__main__":
    main()
