"""默认评分策略：融合归一 + 覆盖率 + 难度/时长微调；仅作同缺料数内决胜分。"""

from __future__ import annotations

from app.config import Settings, get_settings
from app.core.retriever import RecipeCandidate
from app.core.scoring import ScoringStrategy


class DefaultScoringStrategy(ScoringStrategy):
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def score(
        self,
        candidate: RecipeCandidate,
        query: str,
        fusion_norm: float | None = None,
    ) -> float:
        s = self._settings
        norm = candidate.match_score if fusion_norm is None else fusion_norm
        total = candidate.essential_total
        coverage = (
            (total - len(candidate.missing_ingredients)) / total if total > 0 else 1.0
        )
        difficulty_bonus = {1: 1.0, 2: 0.5}.get(candidate.difficulty, 0.0)
        cook = candidate.cook_time_minutes
        time_bonus = (
            1.0
            if cook is not None and cook <= 30
            else 0.5
            if cook is not None and cook <= 60
            else 0.0
        )
        return (
            s.scoring_w_fusion * max(0.0, norm)
            + s.scoring_w_coverage * max(0.0, min(1.0, coverage))
            + s.scoring_w_difficulty * difficulty_bonus
            + s.scoring_w_time * time_bonus
        )
