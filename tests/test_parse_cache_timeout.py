"""P6.4 parse 硬超时 + 结果缓存（命中跳过 LLM / 顺序无关 / 失败不缓存）。"""

import asyncio

import app.graph.nodes as nodes
from app.core.ttl_cache import TTLCache
from app.graph.state import CookState
from app.schemas.recommend import IngredientExtraction, IngredientExtractionList


class _FakeProvider:
    def __init__(self, items=None, delay=0.0, fail=False):
        self.calls = 0
        self._items = items or [IngredientExtraction(name="番茄")]
        self._delay = delay
        self._fail = fail

    async def structured(self, prompt, schema):
        self.calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._fail:
            raise RuntimeError("llm down")
        return IngredientExtractionList(items=self._items)


def _enable_cache(monkeypatch, ttl=60):
    monkeypatch.setattr(nodes, "_parse_cache", TTLCache(ttl_seconds=ttl))


def test_parse_cache_hit_skips_provider(monkeypatch):
    provider = _FakeProvider()
    monkeypatch.setattr(nodes, "get_llm_provider", lambda: provider)
    _enable_cache(monkeypatch)
    r1 = asyncio.run(nodes.parse_node(CookState(ingredients=["番茄", "鸡蛋"])))
    assert provider.calls == 1
    assert r1["parse_error"] is False
    r2 = asyncio.run(nodes.parse_node(CookState(ingredients=["番茄", "鸡蛋"])))
    assert provider.calls == 1  # 命中缓存
    assert [i.raw_name for i in r2["parsed_ingredients"]] == ["番茄"]
    # 键顺序无关
    r3 = asyncio.run(nodes.parse_node(CookState(ingredients=["鸡蛋", "番茄"])))
    assert provider.calls == 1
    assert r3["parse_error"] is False


def test_parse_timeout_goes_to_retry(monkeypatch):
    provider = _FakeProvider(delay=5.0)
    monkeypatch.setattr(nodes, "get_llm_provider", lambda: provider)
    _enable_cache(monkeypatch)
    real_get_settings = nodes.get_settings
    monkeypatch.setattr(
        nodes,
        "get_settings",
        lambda: real_get_settings().model_copy(
            update={"llm_parse_timeout_seconds": 0.2}
        ),
    )
    result = asyncio.run(nodes.parse_node(CookState(ingredients=["番茄"])))
    assert result["parse_error"] is True
    assert result["retry_count"] == 1
    assert result["parsed_ingredients"] == []


def test_parse_failure_not_cached(monkeypatch):
    provider = _FakeProvider(fail=True)
    monkeypatch.setattr(nodes, "get_llm_provider", lambda: provider)
    _enable_cache(monkeypatch)
    r1 = asyncio.run(nodes.parse_node(CookState(ingredients=["番茄"])))
    assert r1["parse_error"] is True
    r2 = asyncio.run(nodes.parse_node(CookState(ingredients=["番茄"])))
    assert provider.calls == 2  # 失败结果不缓存


def test_parse_cache_disabled_by_default_env():
    # conftest 默认 PARSE_CACHE_TTL_SECONDS=0 → 模块级缓存关闭
    assert nodes._parse_cache.enabled is False
