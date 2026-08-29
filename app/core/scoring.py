"""ScoringStrategy 抽象接口。"""

from abc import ABC, abstractmethod

from app.core.retriever import RecipeCandidate


class ScoringStrategy(ABC):
    """评分策略抽象；新增评分因子 = 新策略类 + 配置权重。"""

    @abstractmethod
    async def score(self, candidate: RecipeCandidate, query: str) -> float:
        """返回候选得分（越高越靠前）。"""
        raise NotImplementedError
