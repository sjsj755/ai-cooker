"""Retriever 抽象接口 + RecipeCandidate。"""

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field


class RecipeCandidate(BaseModel):
    """检索候选；missing_ingredients 驱动缺料提示。"""

    recipe_id: int
    title: str
    match_score: float = 0.0
    missing_ingredients: list[str] = Field(default_factory=list)
    # P2 内部字段（不进 API 出参）：缺料计算与评分使用
    essential_total: int = 0
    degraded: bool = False
    difficulty: int | None = None
    cook_time_minutes: int | None = None


class Retriever(ABC):
    """检索器抽象：MVP 为 HybridRetriever（BM25 + Chroma），可替换 Elasticsearch 实现。"""

    @abstractmethod
    async def retrieve(self, query: str, top_k: int = 50) -> list[RecipeCandidate]:
        """按查询召回候选；失败时由实现内部降级（如仅 BM25）。"""
        raise NotImplementedError
