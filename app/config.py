"""应用配置：从环境变量 / .env 读取。"""

from functools import lru_cache

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
