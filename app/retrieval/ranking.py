"""RankingService：retrieve → 缺料 → 忌口过滤 → 融合归一 → 评分 → 字典序排序。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable

from sqlalchemy import select

from app.config import Settings, get_settings
from app.core.html_clean import clean_text
from app.core.retriever import RecipeCandidate
from app.db.session import SessionLocal
from app.models import RecipeTag, Tag
from app.retrieval.errors import RetrievalUnavailableError
from app.retrieval.hybrid import HybridRetriever
from app.retrieval.missing import MissingIngredientsCalculator, MissingInfo
from app.retrieval.scoring import DefaultScoringStrategy

EMPTY_RESULT_NOTICE = "未找到匹配菜谱，可补充食材或放宽忌口"


@dataclass
class RankResult:
    recipes: list[RecipeCandidate] = field(default_factory=list)
    degraded: bool = False
    notice: str | None = None


class RankingService:
    """编排检索全流程；MySQL 异常统一抛 RetrievalUnavailableError（API 转 503）。"""

    def __init__(
        self,
        *,
        retriever=None,
        missing_calculator: MissingIngredientsCalculator | None = None,
        scoring=None,
        settings: Settings | None = None,
        session_factory: Callable = SessionLocal,
    ) -> None:
        self._settings = settings or get_settings()
        self._session_factory = session_factory
        self._retriever = retriever or HybridRetriever(
            settings=self._settings, session_factory=session_factory
        )
        self._missing = missing_calculator or MissingIngredientsCalculator(
            session_factory
        )
        self._scoring = scoring or DefaultScoringStrategy(self._settings)

    async def rank(
        self,
        query: str,
        available_ingredients: list[str] | None = None,
        exclude_tags: list[str] | None = None,
        top_k: int | None = None,
    ) -> RankResult:
        top_k = top_k or self._settings.retrieval_top_k
        available = [
            clean_text(x) for x in (available_ingredients or []) if clean_text(x)
        ]
        excluded = [clean_text(x) for x in (exclude_tags or []) if clean_text(x)]

        candidates = await self._retriever.retrieve(
            query, self._settings.retrieval_top_k
        )
        degraded = any(c.degraded for c in candidates)
        if not candidates:
            return RankResult([], degraded, EMPTY_RESULT_NOTICE)

        ids = [c.recipe_id for c in candidates]
        missing_map = await asyncio.to_thread(
            self._missing.for_recipes, ids, available
        )
        for c in candidates:
            info = missing_map.get(c.recipe_id, MissingInfo(0, []))
            c.essential_total = info.essential_total
            c.missing_ingredients = info.missing_ingredients

        if excluded:
            excluded_ids = await asyncio.to_thread(self._excluded_ids, ids, excluded)
            if excluded_ids:
                candidates = [c for c in candidates if c.recipe_id not in excluded_ids]
        if not candidates:
            return RankResult([], degraded, EMPTY_RESULT_NOTICE)

        max_score = max((c.match_score for c in candidates), default=0.0)
        normalized = {
            c.recipe_id: (c.match_score / max_score if max_score > 0 else 0.0)
            for c in candidates
        }
        scores = await asyncio.gather(
            *(
                self._scoring.score(c, query, fusion_norm=normalized[c.recipe_id])
                for c in candidates
            )
        )
        scored = list(zip(candidates, scores))
        # 覆盖率先行：缺料数升序 → 评分降序 → recipe_id 升序
        scored.sort(
            key=lambda pair: (
                len(pair[0].missing_ingredients),
                -pair[1],
                pair[0].recipe_id,
            )
        )
        ranked = [c for c, _ in scored[:top_k]]
        notice = getattr(self._retriever, "last_notice", None)
        return RankResult(ranked, degraded, notice)

    def _excluded_ids(
        self, recipe_ids: list[int], tag_names: list[str]
    ) -> set[int]:
        if not recipe_ids or not tag_names:
            return set()
        with self._session_factory() as session:
            rows = session.execute(
                select(RecipeTag.recipe_id)
                .join(Tag, Tag.id == RecipeTag.tag_id)
                .where(
                    RecipeTag.recipe_id.in_(recipe_ids),
                    Tag.name.in_(tag_names),
                )
            ).all()
        return {r[0] for r in rows}


@lru_cache
def get_ranking_service() -> RankingService:
    """默认编排服务（API 与 LangGraph 节点共用；测试可 monkeypatch/覆盖）。"""
    return RankingService()
