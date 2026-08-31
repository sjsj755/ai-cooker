"""P6.4 快路径 + 后台 AI 补全 + status 状态轮询。

覆盖：快路径秒出 MySQL 原文且不依赖 generate LLM；后台补全写长缓存；
失败标记生命周期（写入顺序/过期/淘汰）；status 五态与优先级；
返回结果深拷贝（嵌套结构不被消费方改坏）。
"""

import asyncio
import time

from fastapi.testclient import TestClient

import app.api.routes.recommend as rec_mod
from app.core.retriever import RecipeCandidate
from app.core.ttl_cache import TTLCache
from app.graph.state import CookState
from app.main import create_app
from app.schemas.recipes import IngredientItem
from app.schemas.recommend import (
    Recommendation,
    RecommendResponse,
)


def _candidate(recipe_id: int = 1) -> RecipeCandidate:
    return RecipeCandidate(
        recipe_id=recipe_id,
        title="番茄炒蛋",
        match_score=0.01,
        difficulty=1,
        cook_time_minutes=10,
    )


def _recommendation(recipe_id: int = 1, *, steps=None, tips=None) -> Recommendation:
    return Recommendation(
        recipe_id=recipe_id,
        title="番茄炒蛋",
        match_score=0.01,
        missing_ingredients=[],
        difficulty=1,
        cook_time_minutes=10,
        steps=steps,
        tips=tips,
        seasonings=[IngredientItem(name="盐", amount="适量")],
    )


def _fast_state(state: CookState | None = None) -> dict:
    """模拟快路径图返回的完整状态（含 ranked，触发后台补全）。

    传入真实 state 时保留其字段（含 fast_first/ai_pending），便于断言路由透传。
    """
    base = (
        state.model_dump()
        if state is not None
        else CookState(
            query="番茄 鸡蛋",
            ingredients=["番茄", "鸡蛋"],
            ranked=[_candidate()],
            candidates=[_candidate()],
        ).model_dump()
    )
    return {
        **base,
        "candidates": [_candidate()],
        "ranked": [_candidate()],
        "recommendations": [
            _recommendation(steps=[{"instruction": "原文步骤"}], tips=None)
        ],
        "degraded": True,
        "notice": "AI 文案生成中，稍后自动更新",
        "ai_pending": bool(base.get("fast_first", False)),
    }


class _FakeFastGraph:
    """模拟快路径图：返回 MySQL 原文推荐 + ai_pending，不调用 generate。"""

    def __init__(self, calls: list) -> None:
        self._calls = calls

    async def ainvoke(self, state):
        self._calls.append(state)
        return _fast_state(state)


async def _fake_generate_success(state: CookState) -> dict:
    return {
        **state.model_dump(),
        "recommendations": [
            _recommendation(
                steps=[{"instruction": "原文步骤"}], tips="建议加少许糖"
            )
        ],
        "degraded": False,
        "notice": None,
        "ai_pending": False,
    }


async def _fake_generate_degraded(state: CookState) -> dict:
    return {
        **state.model_dump(),
        "recommendations": [
            _recommendation(steps=[{"instruction": "原文步骤"}], tips=None)
        ],
        "degraded": True,
        "notice": "AI 文案不可用，已展示菜谱原文",
        "ai_pending": False,
    }


async def _fake_generate_raise(state: CookState) -> dict:
    raise RuntimeError("deepseek down")


def _make_client(monkeypatch, graph=None):
    calls: list = []
    monkeypatch.setattr(rec_mod, "build_graph", lambda: graph or _FakeFastGraph(calls))
    monkeypatch.setattr(rec_mod, "_recommend_cache", TTLCache(ttl_seconds=60))
    monkeypatch.setattr(rec_mod, "_degraded_cache", TTLCache(ttl_seconds=60))
    monkeypatch.setattr(rec_mod, "_ai_warm_tasks", {})
    monkeypatch.setattr(rec_mod, "_ai_warm_failed_at", {})
    return calls, TestClient(create_app())


def test_fast_path_returns_original_and_spawns_warm(monkeypatch):
    spawned: list[tuple] = []
    monkeypatch.setattr(
        rec_mod,
        "_spawn_ai_warm",
        lambda key, state: spawned.append((key, state)),
    )
    calls, client = _make_client(monkeypatch)
    with client:
        resp = client.post(
            "/api/recipes/recommend",
            json={"ingredients": ["番茄", "鸡蛋"], "exclude_tags": []},
        )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ai_pending"] is True
    assert payload["degraded"] is True
    assert payload["recipes"][0]["steps"][0]["instruction"] == "原文步骤"
    assert payload["recipes"][0]["tips"] is None
    assert len(spawned) == 1
    key, state = spawned[0]
    assert key == (("番茄", "鸡蛋"), ())
    assert state["fast_first"] is True
    assert len(calls) == 1


def test_fast_path_second_request_hits_degraded_cache(monkeypatch):
    monkeypatch.setattr(rec_mod, "_spawn_ai_warm", lambda key, state: None)
    calls, client = _make_client(monkeypatch)
    with client:
        r1 = client.post(
            "/api/recipes/recommend",
            json={"ingredients": ["番茄", "鸡蛋"], "exclude_tags": []},
        )
        r2 = client.post(
            "/api/recipes/recommend",
            json={"ingredients": ["鸡蛋", "番茄"], "exclude_tags": []},
        )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert len(calls) == 1  # 第二次命中降级快缓存，图不再跑


def test_ai_warm_success_sets_long_cache(monkeypatch):
    monkeypatch.setattr(rec_mod, "generate_node", _fake_generate_success)
    monkeypatch.setattr(rec_mod, "_recommend_cache", TTLCache(ttl_seconds=60))
    monkeypatch.setattr(rec_mod, "_ai_warm_tasks", {})
    monkeypatch.setattr(rec_mod, "_ai_warm_failed_at", {})
    key = (("番茄", "鸡蛋"), ())
    asyncio.run(rec_mod._warm_ai_task(key, _fast_state()))
    cached = rec_mod._recommend_cache.get(key)
    assert cached is not None
    assert cached.ai_pending is False
    assert cached.degraded is False
    assert cached.recipes[0].tips == "建议加少许糖"
    assert rec_mod._ai_warm_tasks == {}
    assert key not in rec_mod._ai_warm_failed_at


def test_ai_warm_failure_records_marker_no_long_cache(monkeypatch):
    monkeypatch.setattr(rec_mod, "generate_node", _fake_generate_raise)
    monkeypatch.setattr(rec_mod, "_recommend_cache", TTLCache(ttl_seconds=60))
    monkeypatch.setattr(rec_mod, "_ai_warm_tasks", {})
    monkeypatch.setattr(rec_mod, "_ai_warm_failed_at", {})
    key = (("番茄", "鸡蛋"), ())
    asyncio.run(rec_mod._warm_ai_task(key, _fast_state()))
    assert rec_mod._recommend_cache.get(key) is None
    assert rec_mod._ai_warm_tasks == {}
    assert rec_mod._warm_failed_recently(key) is True


def test_ai_warm_degraded_records_marker(monkeypatch):
    monkeypatch.setattr(rec_mod, "generate_node", _fake_generate_degraded)
    monkeypatch.setattr(rec_mod, "_recommend_cache", TTLCache(ttl_seconds=60))
    monkeypatch.setattr(rec_mod, "_ai_warm_tasks", {})
    monkeypatch.setattr(rec_mod, "_ai_warm_failed_at", {})
    key = (("番茄", "鸡蛋"), ())
    asyncio.run(rec_mod._warm_ai_task(key, _fast_state()))
    assert rec_mod._recommend_cache.get(key) is None
    assert rec_mod._warm_failed_recently(key) is True


def test_ai_warm_single_flight(monkeypatch):
    async def slow_generate(state: CookState) -> dict:
        await asyncio.sleep(0.05)
        return await _fake_generate_success(state)

    monkeypatch.setattr(rec_mod, "generate_node", slow_generate)
    monkeypatch.setattr(rec_mod, "_recommend_cache", TTLCache(ttl_seconds=60))
    monkeypatch.setattr(rec_mod, "_ai_warm_tasks", {})
    monkeypatch.setattr(rec_mod, "_ai_warm_failed_at", {})
    key = (("番茄", "鸡蛋"), ())

    async def scenario():
        rec_mod._spawn_ai_warm(key, _fast_state())
        rec_mod._spawn_ai_warm(key, _fast_state())
        assert len(rec_mod._ai_warm_tasks) == 1
        await asyncio.gather(*rec_mod._ai_warm_tasks.values())

    asyncio.run(scenario())
    assert rec_mod._recommend_cache.get(key) is not None


def test_status_five_states_and_priority(monkeypatch):
    key = (("番茄", "鸡蛋"), ())
    monkeypatch.setattr(rec_mod, "_recommend_cache", TTLCache(ttl_seconds=60))
    monkeypatch.setattr(rec_mod, "_degraded_cache", TTLCache(ttl_seconds=60))
    monkeypatch.setattr(rec_mod, "_ai_warm_tasks", {})
    monkeypatch.setattr(rec_mod, "_ai_warm_failed_at", {})
    _, client = _make_client(monkeypatch)
    with client:
        # 1) 全无 → warming=false
        r = client.post(
            "/api/recipes/recommend/status",
            json={"ingredients": ["番茄", "鸡蛋"], "exclude_tags": []},
        )
        assert r.json() == {"ready": False, "warming": False, "result": None}
        # 2) 仅降级快缓存 → warming=true
        rec_mod._degraded_cache.set(
            key, RecommendResponse(recipes=[], degraded=True, ai_pending=True)
        )
        r = client.post(
            "/api/recipes/recommend/status",
            json={"ingredients": ["番茄", "鸡蛋"], "exclude_tags": []},
        )
        assert r.json()["warming"] is True
        # 3) 近期失败优先于降级缓存 → warming=false（关键顺序）
        rec_mod._ai_warm_failed_at[key] = time.monotonic()
        r = client.post(
            "/api/recipes/recommend/status",
            json={"ingredients": ["番茄", "鸡蛋"], "exclude_tags": []},
        )
        assert r.json() == {"ready": False, "warming": False, "result": None}
        # 4) 在飞优先于失败标记 → warming=true
        rec_mod._ai_warm_tasks[key] = object()  # 哨兵：status 只做成员判断
        r = client.post(
            "/api/recipes/recommend/status",
            json={"ingredients": ["番茄", "鸡蛋"], "exclude_tags": []},
        )
        assert r.json()["warming"] is True
        rec_mod._ai_warm_tasks.pop(key, None)
        # 5) 长缓存就绪 → ready=true + 深拷贝结果
        ai = RecommendResponse(
            recipes=[_recommendation(steps=[{"instruction": "原文"}], tips="建议")],
            degraded=False,
        )
        rec_mod._recommend_cache.set(key, ai)
        r = client.post(
            "/api/recipes/recommend/status",
            json={"ingredients": ["番茄", "鸡蛋"], "exclude_tags": []},
        )
        payload = r.json()
        assert payload["ready"] is True
        assert payload["result"]["recipes"][0]["tips"] == "建议"


def test_status_ready_deep_copy_isolated(monkeypatch):
    key = (("番茄", "鸡蛋"), ())
    _, client = _make_client(monkeypatch)
    canonical = RecommendResponse(
        recipes=[
            _recommendation(
                steps=[{"instruction": "原文步骤"}],
                tips="建议",
            )
        ],
        degraded=False,
    )
    rec_mod._recommend_cache.set(key, canonical)
    with client:
        r1 = client.post(
            "/api/recipes/recommend/status",
            json={"ingredients": ["番茄", "鸡蛋"], "exclude_tags": []},
        )
        mut = r1.json()["result"]
        mut["recipes"][0]["steps"][0]["instruction"] = "被篡改"
        mut["recipes"][0]["seasonings"][0]["name"] = "被篡改"
        mut["recipes"][0]["missing_ingredients"].append("被篡改")
        r2 = client.post(
            "/api/recipes/recommend/status",
            json={"ingredients": ["番茄", "鸡蛋"], "exclude_tags": []},
        )
        fresh = r2.json()["result"]
    assert fresh["recipes"][0]["steps"][0]["instruction"] == "原文步骤"
    assert fresh["recipes"][0]["seasonings"][0]["name"] == "盐"
    assert fresh["recipes"][0]["missing_ingredients"] == []


def test_recommend_hits_long_cache_after_warm(monkeypatch):
    key = (("番茄", "鸡蛋"), ())
    calls, client = _make_client(monkeypatch)
    ai = RecommendResponse(
        recipes=[_recommendation(steps=[{"instruction": "原文"}], tips="建议")],
        degraded=False,
    )
    rec_mod._recommend_cache.set(key, ai)
    with client:
        r = client.post(
            "/api/recipes/recommend",
            json={"ingredients": ["鸡蛋", "番茄"], "exclude_tags": []},
        )
    assert r.status_code == 200
    assert len(calls) == 0  # 长缓存命中，图不再跑
    assert r.json()["recipes"][0]["tips"] == "建议"
    assert r.json()["ai_pending"] is False


def test_failure_marker_expires_and_evicts(monkeypatch):
    monkeypatch.setattr(rec_mod, "_ai_warm_failed_at", {})
    old_key = ("旧", ())
    rec_mod._ai_warm_failed_at[old_key] = time.monotonic() - 9999
    assert rec_mod._warm_failed_recently(old_key) is False
    assert old_key not in rec_mod._ai_warm_failed_at
    # 超过上限淘汰最早时间戳
    monkeypatch.setattr(
        rec_mod,
        "_settings",
        type(
            "S",
            (),
            {
                "recommend_cache_max_entries": 2,
                "recommend_cache_degraded_ttl_seconds": 30,
            },
        )(),
    )
    rec_mod._record_ai_warm_failure(("a", ()))
    rec_mod._record_ai_warm_failure(("b", ()))
    rec_mod._record_ai_warm_failure(("c", ()))
    assert len(rec_mod._ai_warm_failed_at) <= 2
