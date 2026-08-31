"""generate：LLM 成功、防幻觉丢弃、steps 回填、降级 MySQL 补全与 503。"""

import asyncio

import pytest

from app.config import Settings
from app.core.retriever import RecipeCandidate
from app.graph.nodes import generate_node
from app.graph.state import Recommendation, empty_state
from app.retrieval.errors import RetrievalUnavailableError
from app.schemas.recommend import RecommendationSet
from tests.helpers import FakeLLM, add_recipe, delete_recipe

URL = "https://test.generate/1"
TITLE = "生成测试土豆鸡蛋菜"
STEPS = [{"instruction": "土豆切块", "minutes": 5}]


def _seed_recipe(steps=STEPS, seasonings=()):
    rid = add_recipe(
        TITLE,
        URL,
        ingredients=["土豆", "鸡蛋"],
        seasonings=seasonings,
        tags=["家常菜"],
        steps=steps,
    )
    return rid


def _state(rid, ranked=None):
    state = empty_state(ingredients=["土豆", "鸡蛋"])
    state.ranked = ranked or [
        RecipeCandidate(
            recipe_id=rid,
            title=TITLE,
            match_score=0.9,
            missing_ingredients=[],
            difficulty=1,
            cook_time_minutes=20,
        )
    ]
    return state


def _run(coro):
    return asyncio.run(coro)


def test_generate_success_keeps_llm_steps(monkeypatch):
    rid = _seed_recipe()
    try:
        llm = FakeLLM(
            parse_items=[("土豆",)],
            recommendation_set=RecommendationSet(
                recommendations=[
                    Recommendation(
                        recipe_id=rid,
                        title=TITLE,
                        match_score=0.9,
                        missing_ingredients=[],
                        steps=[{"instruction": "LLM 步骤"}],
                        tips="建议",
                    )
                ]
            ),
        )
        monkeypatch.setattr("app.graph.nodes.get_llm_provider", lambda: llm)
        result = _run(generate_node(_state(rid)))
        assert result["degraded"] is False
        assert result["recommendations"][0].steps == [{"instruction": "LLM 步骤"}]
        assert result["recommendations"][0].tips == "建议"
    finally:
        delete_recipe(URL)


def test_generate_drops_hallucinated_recipe_id(monkeypatch):
    rid = _seed_recipe()
    try:
        events = []
        monkeypatch.setattr(
            "app.graph.nodes.log_event",
            lambda logger, level, event, **fields: events.append(
                {"event": event, "fields": fields}
            ),
        )
        llm = FakeLLM(
            parse_items=[("土豆",)],
            recommendation_set=RecommendationSet(
                recommendations=[
                    Recommendation(
                        recipe_id=999999,  # 不在候选集 → 幻觉
                        title="幻觉菜",
                        match_score=1.0,
                    ),
                    Recommendation(
                        recipe_id=rid,
                        title=TITLE,
                        match_score=0.9,
                        steps=STEPS,
                    ),
                ]
            ),
        )
        monkeypatch.setattr("app.graph.nodes.get_llm_provider", lambda: llm)
        result = _run(generate_node(_state(rid)))
        recs = result["recommendations"]
        assert len(recs) == 1
        assert recs[0].recipe_id == rid
        hallucination_logs = [
            e for e in events if e["event"] == "graph.generate.hallucination_dropped"
        ]
        assert len(hallucination_logs) == 1
        assert hallucination_logs[0]["fields"]["dropped"] == 1
    finally:
        delete_recipe(URL)


def test_generate_overwrites_fabricated_data_fields_from_candidate(monkeypatch):
    """不能乱编：LLM 改写 title/分数/难度等数据字段时，一律以候选集为准。"""
    rid = _seed_recipe()
    try:
        llm = FakeLLM(
            parse_items=[("土豆",)],
            recommendation_set=RecommendationSet(
                recommendations=[
                    Recommendation(
                        recipe_id=rid,
                        title="编造的菜名",
                        match_score=99.9,
                        missing_ingredients=["虚构缺料"],
                        difficulty=9,
                        cook_time_minutes=999,
                        steps=[{"instruction": "LLM 步骤"}],
                        tips="建议",
                    )
                ]
            ),
        )
        monkeypatch.setattr("app.graph.nodes.get_llm_provider", lambda: llm)
        result = _run(generate_node(_state(rid)))
        rec = result["recommendations"][0]
        assert rec.title == TITLE
        assert rec.match_score == 0.9
        assert rec.missing_ingredients == []
        assert rec.difficulty == 1
        assert rec.cook_time_minutes == 20
        # LLM 只保留文案类内容
        assert rec.steps == [{"instruction": "LLM 步骤"}]
        assert rec.tips == "建议"
    finally:
        delete_recipe(URL)


def test_generate_deduplicates_and_follows_candidate_order(monkeypatch):
    rid1 = _seed_recipe()
    url2 = "https://test.generate/2"
    rid2 = add_recipe("第二个菜", url2, ingredients=["土豆"], steps=STEPS)
    try:
        llm = FakeLLM(
            parse_items=[("土豆",)],
            recommendation_set=RecommendationSet(
                recommendations=[
                    Recommendation(recipe_id=rid2, title="第二", match_score=0.5, steps=STEPS),
                    Recommendation(recipe_id=rid1, title="第一", match_score=0.9, steps=STEPS),
                    Recommendation(recipe_id=rid2, title="第二重复", match_score=0.5, steps=STEPS),
                ]
            ),
        )
        monkeypatch.setattr("app.graph.nodes.get_llm_provider", lambda: llm)
        state = empty_state(ingredients=["土豆"])
        state.ranked = [
            RecipeCandidate(
                recipe_id=rid1,
                title=TITLE,
                match_score=0.9,
                missing_ingredients=[],
                difficulty=1,
                cook_time_minutes=20,
            ),
            RecipeCandidate(
                recipe_id=rid2,
                title="第二个菜",
                match_score=0.5,
                missing_ingredients=[],
                difficulty=1,
                cook_time_minutes=20,
            ),
        ]
        result = _run(generate_node(state))
        assert [r.recipe_id for r in result["recommendations"]] == [rid1, rid2]
    finally:
        delete_recipe(URL)
        delete_recipe(url2)


def test_generate_fills_empty_steps_from_mysql(monkeypatch):
    rid = _seed_recipe()
    try:
        llm = FakeLLM(
            parse_items=[("土豆",)],
            recommendation_set=RecommendationSet(
                recommendations=[
                    Recommendation(
                        recipe_id=rid,
                        title=TITLE,
                        match_score=0.9,
                        missing_ingredients=[],
                        steps=[],  # LLM 未给出步骤 → 回填 MySQL
                    )
                ]
            ),
        )
        monkeypatch.setattr("app.graph.nodes.get_llm_provider", lambda: llm)
        result = _run(generate_node(_state(rid)))
        assert result["recommendations"][0].steps == STEPS
    finally:
        delete_recipe(URL)


def test_generate_timeout_degrades_to_mysql(monkeypatch):
    """P6.3：generate 硬超时（LLM 拥堵）→ 秒级降级直出 MySQL 原文。"""
    rid = _seed_recipe()
    try:
        class SlowLLM:
            async def structured(self, prompt, schema):
                await asyncio.sleep(5)
                return RecommendationSet(recommendations=[])

        monkeypatch.setattr("app.graph.nodes.get_llm_provider", lambda: SlowLLM())
        monkeypatch.setattr(
            "app.graph.nodes.get_settings",
            lambda: Settings(llm_generate_timeout_seconds=0.2),
        )
        result = _run(generate_node(_state(rid)))
        recs = result["recommendations"]
        assert len(recs) == 1
        assert recs[0].recipe_id == rid
        assert recs[0].steps == STEPS  # MySQL 原文回填
        assert recs[0].tips is None
        assert result["degraded"] is True
    finally:
        delete_recipe(URL)


def test_generate_degrade_fills_from_mysql(monkeypatch):
    rid = _seed_recipe()
    try:
        llm = FakeLLM(parse_items=[("土豆",)], fail_generate=True)
        monkeypatch.setattr("app.graph.nodes.get_llm_provider", lambda: llm)
        result = _run(generate_node(_state(rid)))
        assert result["degraded"] is True
        assert result["notice"] == "AI 文案不可用，已展示菜谱原文"
        rec = result["recommendations"][0]
        assert rec.recipe_id == rid
        assert rec.steps == STEPS
        assert rec.difficulty == 1
        assert rec.cook_time_minutes == 20
        assert rec.tips is None
    finally:
        delete_recipe(URL)


def test_generate_degrade_mysql_failure_raises_unavailable(monkeypatch):
    rid = _seed_recipe()
    try:
        llm = FakeLLM(parse_items=[("土豆",)], fail_generate=True)
        monkeypatch.setattr("app.graph.nodes.get_llm_provider", lambda: llm)

        def boom():
            raise RuntimeError("mysql down")

        monkeypatch.setattr("app.graph.nodes.SessionLocal", boom)
        with pytest.raises(RetrievalUnavailableError):
            _run(generate_node(_state(rid)))
    finally:
        delete_recipe(URL)


def test_generate_no_ranked_returns_empty_notice(monkeypatch):
    rid = _seed_recipe()
    try:
        llm = FakeLLM(parse_items=[("土豆",)], fail_generate=True)
        monkeypatch.setattr("app.graph.nodes.get_llm_provider", lambda: llm)
        result = _run(generate_node(empty_state()))
        assert result["recommendations"] == []
        assert result["notice"] == "未找到匹配菜谱，可补充食材或放宽忌口"
    finally:
        delete_recipe(URL)


def test_generate_success_backfills_seasonings_from_mysql(monkeypatch):
    """成功路径：调料以 MySQL 为准回填（LLM 伪造的 seasonings 一律覆盖）。"""
    rid = _seed_recipe(seasonings=[("盐", "适量"), ("食用油", "少许")])
    try:
        llm = FakeLLM(
            parse_items=[("土豆",)],
            recommendation_set=RecommendationSet(
                recommendations=[
                    Recommendation(
                        recipe_id=rid,
                        title="编造标题",
                        match_score=99.9,
                        missing_ingredients=["虚构缺料"],
                        steps=[{"instruction": "LLM 步骤"}],
                        tips="建议",
                        seasonings=[{"name": "编造调料"}],
                    )
                ]
            ),
        )
        monkeypatch.setattr("app.graph.nodes.get_llm_provider", lambda: llm)
        result = _run(generate_node(_state(rid)))
        rec = result["recommendations"][0]
        assert [(s.name, s.amount) for s in rec.seasonings] == [
            ("盐", "适量"),
            ("食用油", "少许"),
        ]
    finally:
        delete_recipe(URL)


def test_generate_degrade_backfills_seasonings_from_mysql(monkeypatch):
    """降级路径：MySQL 原文直出时同样回填调料。"""
    rid = _seed_recipe(seasonings=["盐", "食用油"])
    try:
        llm = FakeLLM(parse_items=[("土豆",)], fail_generate=True)
        monkeypatch.setattr("app.graph.nodes.get_llm_provider", lambda: llm)
        result = _run(generate_node(_state(rid)))
        rec = result["recommendations"][0]
        assert [s.name for s in rec.seasonings] == ["盐", "食用油"]
        assert all(s.amount is None for s in rec.seasonings)
    finally:
        delete_recipe(URL)
