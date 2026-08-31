"""应用配置：从环境变量 / .env 读取。"""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.proxy_ip import parse_trusted_networks


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
    # P6.3：LLM 结构化调用重试次数（2 次 = 首败重试 1 次）；高峰期 DeepSeek
    # 偶发慢/超时，减少重试避免用户等 90s+（generate 另有硬超时，见下）
    llm_max_attempts: int = 2
    # P6.3：generate 阶段硬超时（秒）。LLM 文案超时/失败即秒级降级直出
    # MySQL 原文（steps/difficulty/cook_time 完整，仅 tips=None + notice），
    # 保证“首次访问”最坏延迟可预期，而不是随 LLM 拥堵无限变慢
    llm_generate_timeout_seconds: float = 10.0

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

    # P6.1 性能优化：推荐结果内存 TTL 缓存（0=关闭；单进程 uvicorn 内生效，
    # 多 worker 时各进程独立缓存；仅缓存非降级结果，故障不会被“粘住”）
    recommend_cache_ttl_seconds: float = 600.0
    recommend_cache_max_entries: int = 256
    # P6.3：降级结果短 TTL 缓存（秒；0=不缓存降级）。LLM 拥堵期间重复查询
    # 也能秒回；TTL 很短，DeepSeek 恢复后最多 30s 内即返回新结果
    recommend_cache_degraded_ttl_seconds: float = 30.0

    # P6.1 性能优化：启动时后台预热检索（BM25 语料 + Chroma 集合），
    # 避免首个用户承担冷启动（实测冷启动可达 30-60s，前端 recommend 超时 30s）
    warmup_on_startup: bool = True

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

    # P6 部署（可信代理 / 安全加固）
    behind_proxy: bool = False
    forwarded_allow_ips: str = ""
    docs_enabled: bool = True
    allowed_hosts: str = ""
    security_headers_enabled: bool = True

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

    @model_validator(mode="after")
    def _validate_p6_deploy_config(self) -> "Settings":
        """P6 部署配置校验（fail-fast）：反代白名单非空且格式合法。"""
        if self.behind_proxy and not self.forwarded_allow_ips.strip():
            raise ValueError(
                "BEHIND_PROXY=true 时必须配置 FORWARDED_ALLOW_IPS"
                "（可信代理 IP/CIDR 白名单，逗号分隔）"
            )
        if self.forwarded_allow_ips.strip():
            # 白名单格式错误（非 IP/CIDR）直接拒绝启动，避免限流静默按错误白名单计数
            try:
                parse_trusted_networks(self.forwarded_allow_ips)
            except ValueError as exc:
                raise ValueError(
                    f"FORWARDED_ALLOW_IPS 含非法 IP/CIDR 条目：{exc}"
                ) from exc
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
