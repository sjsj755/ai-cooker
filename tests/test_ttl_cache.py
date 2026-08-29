"""TTLCache：启用/禁用、过期、LRU 淘汰、命中刷新。"""

import time

import pytest

from app.core.ttl_cache import TTLCache


def test_get_set_and_enabled():
    cache = TTLCache(ttl_seconds=60)
    assert cache.enabled
    assert cache.get("k") is None
    cache.set("k", {"v": 1})
    assert cache.get("k") == {"v": 1}


def test_disabled_when_ttl_zero():
    cache = TTLCache(ttl_seconds=0)
    assert not cache.enabled
    cache.set("k", 1)
    assert cache.get("k") is None


def test_expired_returns_none(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    cache = TTLCache(ttl_seconds=10)
    cache.set("k", 1)
    now[0] = 110.0
    assert cache.get("k") is None


def test_eviction_drops_oldest():
    cache = TTLCache(ttl_seconds=60, max_entries=2)
    cache.set(1, "a")
    cache.set(2, "b")
    cache.set(3, "c")
    assert cache.get(1) is None
    assert cache.get(2) == "b"
    assert cache.get(3) == "c"


def test_get_refreshes_lru_order():
    cache = TTLCache(ttl_seconds=60, max_entries=2)
    cache.set(1, "a")
    cache.set(2, "b")
    assert cache.get(1) == "a"
    cache.set(3, "c")
    assert cache.get(1) == "a"  # 1 命中后移队尾，2 被淘汰
    assert cache.get(2) is None
    assert cache.get(3) == "c"


def test_clear():
    cache = TTLCache(ttl_seconds=60)
    cache.set(1, "a")
    cache.clear()
    assert cache.get(1) is None


def test_purges_expired_on_set(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    cache = TTLCache(ttl_seconds=10, max_entries=2)
    cache.set(1, "a")
    cache.set(2, "b")
    now[0] = 120.0
    cache.set(3, "c")  # 写入时清理过期项，容量仍够
    assert cache.get(1) is None
    assert cache.get(2) is None
    assert cache.get(3) == "c"
