"""BM25Corpus：bigram 分词、语料别名、缓存探针与失败四态。"""

import asyncio

import pytest
from rank_bm25 import BM25Okapi

from app.retrieval.bm25 import BM25Corpus, tokenize
from app.retrieval.errors import RetrievalUnavailableError


def _row(
    rid: int,
    title: str,
    *,
    description: str = "",
    ingredients=None,
    seasonings=None,
    tags=None,
    difficulty: int | None = 1,
    cook_time: int | None = 20,
    source_url: str | None = None,
) -> dict:
    return {
        "recipe_id": rid,
        "title": title,
        "description": description,
        "difficulty": difficulty,
        "cook_time_minutes": cook_time,
        "source_url": source_url or f"https://example.com/{rid}",
        "ingredients": ingredients or [],
        "seasonings": seasonings or [],
        "tags": tags or [],
    }


def test_tokenize_bigram():
    assert tokenize("土豆鸡蛋") == ["土豆", "豆鸡", "鸡蛋"]
    assert tokenize("abc 123") == ["abc", "123"]
    assert tokenize("椒") == ["椒"]
    assert tokenize("") == []


def test_search_shared_bigram_and_excludes_unrelated():
    rows = [
        _row(1, "土豆鸡蛋饼", ingredients=[{"name": "土豆"}, {"name": "鸡蛋"}]),
        _row(2, "鸡蛋炒番茄", ingredients=[{"name": "鸡蛋"}]),
        _row(3, "红烧牛肉", ingredients=[{"name": "牛肉"}]),
    ]
    corpus = BM25Corpus(loader=lambda: (rows, (3, 3, "t1")))
    asyncio.run(corpus.ensure_built())
    hits = asyncio.run(corpus.search("鸡蛋 土豆", 10))
    ids = [rid for rid, _ in hits]
    assert ids[0] == 1
    assert 2 in ids
    assert 3 not in ids


def test_alias_in_corpus():
    rows = [
        _row(1, "凉拌菜", ingredients=[{"name": "土豆", "aliases": ["马铃薯", "洋芋"]}]),
        _row(2, "红烧鱼", ingredients=[{"name": "鱼"}]),
        _row(3, "清炒时蔬", ingredients=[{"name": "青菜"}]),
    ]
    corpus = BM25Corpus(loader=lambda: (rows, (3, 3, "t1")))
    asyncio.run(corpus.ensure_built())
    hits = asyncio.run(corpus.search("马铃薯", 5))
    assert [rid for rid, _ in hits] == [1]


def test_empty_corpus_is_normal_not_degraded():
    corpus = BM25Corpus(loader=lambda: ([], (0, 0, None)))
    asyncio.run(corpus.ensure_built())
    assert corpus.degraded_notice is None
    assert asyncio.run(corpus.search("土豆", 5)) == []


def test_cache_rebuilds_on_updated_at_change():
    state = {"index": 0}
    titles = ["土豆鸡蛋", "番茄牛肉"]
    stamps = ["2026-08-29T00:00:00.000", "2026-08-29T00:00:01.000"]
    filler = [_row(2, "红烧鱼"), _row(3, "清炒时蔬")]

    def loader():
        i = state["index"]
        return ([_row(1, titles[i])] + filler, (3, 3, stamps[i]))

    corpus = BM25Corpus(loader=loader)
    asyncio.run(corpus.ensure_built())
    assert asyncio.run(corpus.search("番茄", 5)) == []

    state["index"] = 1  # 模拟原地 UPDATE：行数/MAX(id) 不变，仅 updated_at 变化
    asyncio.run(corpus.ensure_built())
    hits = asyncio.run(corpus.search("番茄", 5))
    assert [rid for rid, _ in hits] == [1]


def test_concurrent_ensure_built_no_errors():
    rows = [_row(i, f"测试菜{i}") for i in range(1, 30)]
    corpus = BM25Corpus(loader=lambda: (rows, (29, 29, "t1")))

    async def run():
        await asyncio.gather(*(corpus.ensure_built() for _ in range(8)))
        return await corpus.search("测试菜5", 5)

    hits = asyncio.run(run())
    assert any(rid == 5 for rid, _ in hits)
    assert corpus.degraded_notice is None


def test_loader_failure_without_cache_raises():
    def loader():
        raise RuntimeError("mysql down")

    corpus = BM25Corpus(loader=loader)
    with pytest.raises(RetrievalUnavailableError):
        asyncio.run(corpus.ensure_built())


def test_loader_failure_with_stale_cache_degrades_and_self_heals():
    rows = [
        _row(1, "土豆鸡蛋"),
        _row(2, "红烧鱼"),
        _row(3, "清炒时蔬"),
    ]
    state = {"fail": False}

    def loader():
        if state["fail"]:
            raise RuntimeError("mysql down")
        return (rows, (3, 3, "t1"))

    corpus = BM25Corpus(loader=loader)
    asyncio.run(corpus.ensure_built())
    state["fail"] = True
    asyncio.run(corpus.ensure_built())
    assert corpus.degraded_notice == "关键词索引更新失败，已回退缓存数据"
    # 旧缓存仍可服务
    assert asyncio.run(corpus.search("土豆", 5)) != []

    state["fail"] = False
    asyncio.run(corpus.ensure_built())
    assert corpus.degraded_notice is None


def test_build_failure_without_cache_returns_empty_degraded(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("build boom")

    monkeypatch.setattr("app.retrieval.bm25.BM25Okapi", boom)
    corpus = BM25Corpus(loader=lambda: ([_row(1, "土豆")], (1, 1, "t1")))
    asyncio.run(corpus.ensure_built())
    assert corpus.degraded_notice == "关键词索引构建失败"
    assert asyncio.run(corpus.search("土豆", 5)) == []
