"""P5 mock LLM：零网络 / 确定性 / parse 量词剥离 + 别名映射 / generate 走真实回填。"""

import asyncio

import httpx

from app.config import Settings
from app.core.mock_llm import MockLLMProvider
from app.core.retriever import RecipeCandidate
from app.graph.nodes import get_llm_provider
from app.graph.prompts import generate_prompt, parse_prompt
from app.schemas.recommend import (
    IngredientExtractionList,
    RecommendationSet,
)


def _run(coro):
    return asyncio.run(coro)


def _candidates():
    return [
        RecipeCandidate(
            recipe_id=1,
            title="家常土豆炒鸡蛋",
            match_score=0.012,
            missing_ingredients=[],
            difficulty=1,
            cook_time_minutes=20,
        ),
        RecipeCandidate(
            recipe_id=2,
            title="香辣牛肉炖土豆",
            match_score=0.008,
            missing_ingredients=["辣椒"],
            difficulty=2,
            cook_time_minutes=40,
        ),
        RecipeCandidate(
            recipe_id=3,
            title="红烧豆腐",
            match_score=0.006,
            missing_ingredients=[],
            difficulty=2,
            cook_time_minutes=30,
        ),
    ]


def test_mock_llm_parse_strips_quantifiers_and_maps_aliases():
    provider = MockLLMProvider()
    result = _run(
        provider.structured(
            parse_prompt(["两个土豆、三颗鸡蛋", "一斤牛肉，半斤虾仁"]),
            IngredientExtractionList,
        )
    )
    assert [i.name for i in result.items] == ["土豆", "鸡蛋", "牛肉", "虾"]
    assert [i.quantity for i in result.items] == ["两个", "三颗", "一斤", "半斤"]
    # 别名映射：马铃薯 → 土豆
    result = _run(
        provider.structured(parse_prompt(["马铃薯和番茄"]), IngredientExtractionList)
    )
    assert [i.name for i in result.items] == ["土豆", "西红柿"]


def test_mock_llm_deterministic_same_input_same_output():
    provider = MockLLMProvider()
    prompt = parse_prompt(["两个土豆、三颗鸡蛋"])
    out1 = _run(provider.structured(prompt, IngredientExtractionList))
    out2 = _run(provider.structured(prompt, IngredientExtractionList))
    assert out1.model_dump() == out2.model_dump()


def test_mock_llm_generate_uses_candidate_ids_and_real_validation():
    provider = MockLLMProvider()
    candidates = _candidates()
    result = _run(
        provider.structured(
            generate_prompt(candidates, ["土豆", "鸡蛋"], []),
            RecommendationSet,
        )
    )
    assert len(result.recommendations) == 3
    ids = {r.recipe_id for r in result.recommendations}
    assert ids <= {c.recipe_id for c in candidates}
    for rec in result.recommendations:
        assert isinstance(rec.steps, list) and 2 <= len(rec.steps) <= 3
        assert all(isinstance(s.get("minutes"), int) for s in rec.steps)
        assert isinstance(rec.tips, str) and rec.tips


def test_mock_llm_zero_network(monkeypatch):
    """断言 httpx 未被调用：MockLLMProvider 本身零网络 IO。"""

    def boom(*args, **kwargs):
        raise AssertionError("httpx 不应被调用")

    monkeypatch.setattr(httpx, "AsyncClient", boom)
    monkeypatch.setattr(httpx, "Client", boom)
    provider = MockLLMProvider()
    _run(provider.structured(parse_prompt(["土豆"]), IngredientExtractionList))
    _run(provider.structured(generate_prompt(_candidates(), ["土豆"], []), RecommendationSet))


def test_get_llm_provider_returns_mock_when_llm_mock(monkeypatch):
    from app.graph import nodes

    nodes.get_llm_provider.cache_clear()
    monkeypatch.setattr(
        nodes, "get_settings", lambda: Settings(llm_mock=True, llm_api_key=None)
    )
    try:
        provider = nodes.get_llm_provider()
        assert isinstance(provider, MockLLMProvider)
    finally:
        nodes.get_llm_provider.cache_clear()


def test_mock_llm_recommend_flow_backfills_facts(client, tmp_path, monkeypatch):
    """mock 输出走真实校验与防幻觉回填：事实字段以候选集为准，steps/tips 用 mock 文案。"""
    import app.api.routes.recommend as rec_mod

    from tests.test_recommend_api import TITLE, _patch_deps, _seed_env

    rid, service, _chroma = _seed_env(tmp_path)
    provider = MockLLMProvider()
    _patch_deps(monkeypatch, service, llm=provider)
    monkeypatch.setattr(
        rec_mod,
        "_settings",
        rec_mod._settings.model_copy(
            update={"recommend_fast_first_enabled": False}
        ),
    )
    try:
        resp = client.post(
            "/api/recipes/recommend",
            json={"ingredients": ["土豆", "鸡蛋"], "exclude_tags": []},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["degraded"] is False
        assert len(body["recipes"]) == 1
        rec = body["recipes"][0]
        assert rec["recipe_id"] == rid
        assert rec["title"] == TITLE  # 事实字段回填候选集
        assert rec["steps"]  # mock 提供的 steps 保留（含 minutes）
        assert all("minutes" in s for s in rec["steps"])
        assert rec["tips"]
        assert [(s["name"], s["amount"]) for s in rec["seasonings"]] == [
            ("盐", "适量"),
            ("食用油", "少许"),
        ]
    finally:
        from tests.helpers import delete_recipe

        delete_recipe("https://test.recommend/1")
