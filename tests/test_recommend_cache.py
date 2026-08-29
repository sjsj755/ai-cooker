"""recommend 路由 TTL 缓存：命中秒回、键归一化（顺序无关）、降级不缓存。"""

from fastapi.testclient import TestClient

import app.api.routes.recommend as rec_mod
from app.core.ttl_cache import TTLCache
from app.main import create_app


class _FakeGraph:
    def __init__(self, calls: list, degraded: bool = False) -> None:
        self._calls = calls
        self._degraded = degraded

    async def ainvoke(self, state):
        self._calls.append(tuple(state.ingredients or []))
        return {
            "recommendations": [],
            "degraded": self._degraded,
            "notice": "降级提示" if self._degraded else None,
        }


def _make_client(monkeypatch, degraded: bool = False):
    calls: list = []
    monkeypatch.setattr(rec_mod, "build_graph", lambda: _FakeGraph(calls, degraded))
    monkeypatch.setattr(rec_mod, "_recommend_cache", TTLCache(ttl_seconds=60))
    return calls, TestClient(create_app())


def test_recommend_cache_hits_second_call(monkeypatch):
    calls, client = _make_client(monkeypatch)
    with client:
        r1 = client.post(
            "/api/recipes/recommend",
            json={"ingredients": ["土豆", "鸡蛋"], "exclude_tags": []},
        )
        r2 = client.post(
            "/api/recipes/recommend",
            json={"ingredients": ["鸡蛋", "土豆"], "exclude_tags": []},
        )
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert len(calls) == 1  # 第二请求命中缓存，图只跑了一次


def test_recommend_cache_respects_different_inputs(monkeypatch):
    calls, client = _make_client(monkeypatch)
    with client:
        client.post(
            "/api/recipes/recommend",
            json={"ingredients": ["土豆"], "exclude_tags": []},
        )
        client.post(
            "/api/recipes/recommend",
            json={"ingredients": ["鸡蛋"], "exclude_tags": []},
        )
        assert len(calls) == 2


def test_recommend_cache_skips_degraded(monkeypatch):
    calls, client = _make_client(monkeypatch, degraded=True)
    with client:
        r1 = client.post(
            "/api/recipes/recommend",
            json={"ingredients": ["土豆"], "exclude_tags": []},
        )
        r2 = client.post(
            "/api/recipes/recommend",
            json={"ingredients": ["土豆"], "exclude_tags": []},
        )
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert len(calls) == 2  # 降级结果不缓存，故障恢复后立即生效
