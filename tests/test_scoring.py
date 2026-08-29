"""融合原语不变量、配置校验与覆盖率先行的字典序排序。"""

import asyncio

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.core.retriever import RecipeCandidate
from app.retrieval.fusion import rrf
from app.retrieval.missing import MissingInfo
from app.retrieval.ranking import RankingService
from app.retrieval.scoring import DefaultScoringStrategy

K, W = 60, 0.5


def _candidate(
    recipe_id: int,
    *,
    title: str = "",
    match_score: float = 0.0,
    missing=None,
    essential_total: int = 0,
    difficulty: int | None = None,
    cook_time: int | None = None,
) -> RecipeCandidate:
    return RecipeCandidate(
        recipe_id=recipe_id,
        title=title,
        match_score=match_score,
        missing_ingredients=missing or [],
        essential_total=essential_total,
        difficulty=difficulty,
        cook_time_minutes=cook_time,
    )


def test_rrf_invariants():
    assert rrf([], K, W) == 0.0
    assert abs(rrf([1], K, W) - W / (K + 1)) < 1e-12
    # 两路同位次时融合恰为单路两倍
    assert abs(rrf([1], K, W) * 2 - (rrf([1], K, W) + rrf([1], K, W))) < 1e-12
    # 上界：所有证据 rank=1
    assert rrf([1, 1, 1], K, W) <= W / (K + 1) + 1e-12
    # 单点幸运噪声被均值稀释
    assert rrf([1, 200, 300], K, W) < rrf([2, 3, 4], K, W)


def test_config_validation():
    with pytest.raises(ValidationError):
        Settings(retrieval_bm25_weight=0.6, retrieval_vector_weight=0.3)
    with pytest.raises(ValidationError):
        Settings(retrieval_fusion_rrf_k=0)


async def _score(candidate, fusion_norm=None):
    return await DefaultScoringStrategy(Settings()).score(
        candidate, "土豆", fusion_norm=fusion_norm
    )


def test_scoring_formula():
    s = Settings()
    candidate = _candidate(
        1, match_score=0.8, missing=["青椒"], essential_total=2,
        difficulty=1, cook_time=20,
    )
    score = asyncio.run(_score(candidate, fusion_norm=0.8))
    expected = (
        s.scoring_w_fusion * 0.8
        + s.scoring_w_coverage * 0.5
        + s.scoring_w_difficulty * 1.0
        + s.scoring_w_time * 1.0
    )
    assert abs(score - expected) < 1e-9


def test_coverage_one_when_no_essential():
    candidate = _candidate(1, match_score=0.0, essential_total=0)
    score = asyncio.run(_score(candidate, fusion_norm=0.0))
    assert score == pytest.approx(Settings().scoring_w_coverage)


class _StubRetriever:
    def __init__(self, candidates):
        self.candidates = candidates
        self.last_notice = None

    async def retrieve(self, query, top_k):
        return self.candidates


class _StubMissing:
    def __init__(self, info_by_id):
        self.info_by_id = info_by_id

    def for_recipes(self, recipe_ids, available_names):
        return {rid: self.info_by_id.get(rid, MissingInfo(0, [])) for rid in recipe_ids}


def test_ranking_coverage_first_lexicographic():
    # essential_total=2：缺 1 样且高融合/高加成的候选，不得越过全覆盖低分候选
    missing_one = _candidate(
        1,
        title="缺一样",
        match_score=1.0,
        missing=["青椒"],
        essential_total=2,
        difficulty=1,
        cook_time=15,
    )
    full_cover = _candidate(
        2,
        title="全覆盖",
        match_score=0.01,
        essential_total=2,
        difficulty=3,
        cook_time=90,
    )
    service = RankingService(
        retriever=_StubRetriever([missing_one, full_cover]),
        missing_calculator=_StubMissing(
            {1: MissingInfo(2, ["青椒"]), 2: MissingInfo(2, [])}
        ),
    )
    result = asyncio.run(service.rank("土豆", available_ingredients=["土豆", "鸡蛋"]))
    assert [c.recipe_id for c in result.recipes] == [2, 1]


def test_ranking_stable_tie_by_recipe_id():
    a = _candidate(10, match_score=0.5, essential_total=1, difficulty=1, cook_time=10)
    b = _candidate(20, match_score=0.5, essential_total=1, difficulty=1, cook_time=10)
    service = RankingService(
        retriever=_StubRetriever([a, b]),
        missing_calculator=_StubMissing({10: MissingInfo(1, []), 20: MissingInfo(1, [])}),
    )
    result = asyncio.run(service.rank("土豆"))
    assert [c.recipe_id for c in result.recipes] == [10, 20]
