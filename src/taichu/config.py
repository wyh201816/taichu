"""读取并校验应用配置。"""

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """太初运行配置。"""

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    rightcode_api_key: SecretStr = SecretStr("")
    rightcode_responses_base_url: str = "https://www.rightapi.ai/codex/v1"
    rightcode_claude_sale_base_url: str = "https://www.rightapi.ai/claude"
    rightcode_deepseek_anthropic_base_url: str = (
        "https://rightapi.ai/deepseek/anthropic"
    )
    rightcode_default_model_id: str = "deepseek-v4-pro"
    rightcode_request_timeout_seconds: float = 300
    rightcode_max_retries: int = 2
    rightcode_model_prices_json: str = "{}"
    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_anthropic_base_url: str = "https://api.deepseek.com/anthropic"
    deepseek_fallback_enabled: bool = True
    deepseek_fallback_model_id: str = "deepseek-v4-pro"
    deepseek_fallback_max_retries: int = 1
    agent_model_roles_json: str = "{}"

    host: str = "127.0.0.1"
    port: int = 8000
    backend_reload: bool = True

    project_assets_dir: Path = Path("project_assets")
    evaluation_datasets_dir: Path = Path("tests/fixtures/evaluations")
    evaluation_judge_model: str = ""
    opik_enabled: bool = False
    opik_project_name: str = "taichu-general-agent-benchmark"
    opik_url_override: str = ""
    opik_api_key: SecretStr = SecretStr("")
    opik_workspace: str = ""

    milvus_uri: str = "http://127.0.0.1:19530"
    milvus_token: SecretStr = SecretStr("")
    milvus_collection_prefix: str = "taichu_story"
    milvus_hnsw_m: int = 24
    milvus_hnsw_ef_construction: int = 300
    milvus_hnsw_ef_search: int = 150
    milvus_rrf_k: int = 60

    embedding_base_url: str = "http://127.0.0.1:8011/v1"
    embedding_model_id: str = "Qwen3-Embedding-4B-Q4_K_M"
    embedding_dimensions: int = 2560
    embedding_request_timeout_seconds: float = 120
    embedding_max_input_tokens: int = 8192

    vector_graph_llm_model: str = "deepseek-v4-pro"
    vector_graph_manuscript_chunk_size: int = 1_000
    vector_graph_manuscript_chunk_overlap: int = 200
    vector_graph_passage_top_k: int = 30
    vector_graph_expansion_max_seed_entities: int = 5
    vector_graph_expansion_max_seed_relations: int = 32
    vector_graph_expansion_max_hop: int = 1
    vector_graph_expansion_max_entities_per_hop: int = 20
    vector_graph_expansion_relations_per_entity: int = 10
    vector_graph_expansion_candidate_pool_multiplier: int = 4
    vector_graph_expansion_hub_relations_per_entity: int = 5
    vector_graph_expansion_hub_degree_threshold: int = 100
    vector_graph_expansion_beam_width: int = 24
    vector_graph_expansion_max_total_relations: int = 56
    vector_graph_expansion_max_graph_passages: int = 20
    vector_graph_reranker_top_k: int = 10

    reranker_base_url: str = "http://127.0.0.1:8012"
    reranker_model_id: str = "BAAI/bge-reranker-v2-m3"
    reranker_request_timeout_seconds: float = 180

    general_agent_memory_age_decay_days: int = 30
    general_agent_memory_minimum_relevance: float = 0.01
    general_agent_result_preview_tokens: int = 4_000
    general_agent_model_windows_json: str = "{}"
    general_agent_tokenizers_json: str = "{}"
    general_agent_capability_retrieval_limit: int = 12

    mongodb_home: Path | None = None
    mongodb_data_dir: Path | None = None
    mongodb_log_dir: Path | None = None
    mongodb_uri: str = "mongodb://127.0.0.1:27017"
    mongodb_database: str = "taichu"


settings = Settings()
