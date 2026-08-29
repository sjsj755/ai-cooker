"""应用配置：从环境变量 / .env 读取。"""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """集中管理环境变量；密钥只走环境变量，禁止硬编码入库。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AI 厨师"
    version: str = "0.1.0"
    database_url: str = "mysql+pymysql://ai_cooker:ai_cooker@127.0.0.1:3306/ai_cooker"
    # CORS 默认关闭；仅当显式配置白名单时开启
    cors_origins: list[str] = []

    # P4 前端（FastAPI 同源托管静态资源）
    frontend_dir: str = "./frontend"

    # P1 采集（parse）
    crawler_delay_seconds: float = 10.0
    crawler_timeout_seconds: float = 10.0
    crawler_retry: int = 3
    crawler_allowed_domains: list[str] = ["www.xiachufang.com", "m.xiachufang.com"]
    crawler_ua: str = "ai-cooker-p1-bot/0.1 (+contact: dev@example.com)"
    crawler_output_dir: str = "./data/crawled"
    log_level: str = "INFO"

    # P1 ingest（向量化与入库）
    chroma_dir: str = "./data/chroma"
    chroma_collection: str = "recipe_docs"
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_model: str = "text-embedding-3-small"
    embedding_api_key: str | None = None
    embedding_batch_size: int = 64
    embedding_timeout_seconds: float = 30.0

    # P3 LLM（结构化识别/文案生成；OpenAI 兼容，可切 DeepSeek / Qwen / 本地端点）
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str | None = None
    llm_timeout_seconds: float = 30.0
    llm_temperature: float = 0.2

    # P3 推荐工作流
    recommend_top_k: int = 5
    recommend_max_parse_retries: int = 1
    link_vector_similarity_threshold: float = 0.85

    # P2 检索（BM25 + Chroma 向量 + RRF 融合）
    retrieval_top_k: int = 50
    retrieval_fusion_rrf_k: int = 60
    retrieval_bm25_weight: float = 0.5
    retrieval_vector_weight: float = 0.5
    retrieval_vector_query_multiplier: int = 4
    retrieval_vector_max_distance: float = 0.5

    # P2 评分（融合分仅作同缺料数内的决胜分，排序由 RankingService 字典序保证）
    scoring_w_fusion: float = 0.4
    scoring_w_coverage: float = 0.5
    scoring_w_difficulty: float = 0.05
    scoring_w_time: float = 0.05

    # P2 食材联想向量库
    chroma_ingredients_collection: str = "ingredients_docs"

    # P5 限流（slowapi；默认关闭，本地/测试/压测不打扰，生产开启）
    rate_limit_enabled: bool = False
    rate_limit_storage: str = "memory"  # memory | redis
    rate_limit_redis_url: str = ""
    rate_limit_default_per_minute: int = 100
    rate_limit_recommend_per_minute: int = 10
    rate_limit_feedback_per_minute: int = 20

    # P5 mock LLM（LLM_MOCK=true 时 get_llm_provider 返回 MockLLMProvider：
    # 零网络 IO、确定性输出，供 CI / k6 压测使用）
    llm_mock: bool = False

    # P5 反馈匿名指纹盐（SHA-256(IP + FEEDBACK_SALT)；生产必须由 start.sh 强校验非空）
    feedback_salt: str = ""

    # P5 LangSmith 评测（可选；无 key 时 eval --trace 跳过）
    langsmith_api_key: str | None = None

    @model_validator(mode="after")
    def _validate_retrieval_config(self) -> "Settings":
        if self.retrieval_fusion_rrf_k <= 0:
            raise ValueError("RETRIEVAL_FUSION_RRF_K 必须大于 0")
        weight_sum = self.retrieval_bm25_weight + self.retrieval_vector_weight
        if abs(weight_sum - 1.0) > 1e-6:
            raise ValueError(
                "RETRIEVAL_BM25_WEIGHT 与 RETRIEVAL_VECTOR_WEIGHT 之和必须为 1"
            )
        return self

    @model_validator(mode="after")
    def _validate_rate_limit_config(self) -> "Settings":
        """限流配置校验（fail-fast）：storage 枚举 + redis 必须配 URL。"""
        if self.rate_limit_storage not in {"memory", "redis"}:
            raise ValueError("RATE_LIMIT_STORAGE 必须为 memory 或 redis")
        if self.rate_limit_storage == "redis" and not self.rate_limit_redis_url:
            raise ValueError("RATE_LIMIT_STORAGE=redis 时必须配置 RATE_LIMIT_REDIS_URL")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
