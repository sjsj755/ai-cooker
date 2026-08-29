"""FastAPI 依赖：DB 会话、检索编排服务与食材向量组件。"""

from functools import lru_cache

from app.config import get_settings
from app.core.embeddings import EmbeddingProvider
from app.core.openai_embeddings import EmbeddingConfigError, OpenAICompatibleEmbeddings
from app.db.session import get_db
from app.retrieval.ranking import get_ranking_service
from app.vector_store import ChromaStore

__all__ = [
    "get_db",
    "get_embedding_provider",
    "get_ingredients_chroma",
    "get_ranking_service",
]


@lru_cache
def get_embedding_provider() -> EmbeddingProvider | None:
    """真实嵌入提供者；未配置 key 或构造失败时返回 None（调用方走降级/回退）。"""
    settings = get_settings()
    if not settings.embedding_api_key:
        return None
    try:
        return OpenAICompatibleEmbeddings(settings)
    except EmbeddingConfigError:
        return None


def get_ingredients_chroma() -> ChromaStore:
    """食材联想向量集合（ingredients_docs）。"""
    settings = get_settings()
    return ChromaStore(settings, collection=settings.chroma_ingredients_collection)
