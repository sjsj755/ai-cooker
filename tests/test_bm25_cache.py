"""P6.4 BM25 索引磁盘持久化：探针一致直接加载、变更重建、自定义 loader 绕过。"""

import asyncio

from app.retrieval.bm25 import BM25Corpus


def _rows():
    return [
        {
            "recipe_id": rid,
            "title": title,
            "description": "",
            "difficulty": 1,
            "cook_time_minutes": 5,
            "source_url": "",
            "ingredients": [{"name": name, "aliases": []}],
            "seasonings": [],
            "tags": [],
        }
        for rid, title, name in [
            (1, "番茄炒蛋", "番茄"),
            (2, "土豆炖牛肉", "土豆"),
        ]
    ], (2, 2, "2026-01-01T00:00:00")


def _loader(rows, probe, calls):
    def load():
        calls.append(1)
        return rows, probe

    return load


def test_bm25_cache_round_trip_skips_loader(tmp_path):
    cache_file = str(tmp_path / "bm25.pkl")
    rows, probe = _rows()
    calls1: list = []
    c1 = BM25Corpus(
        cache_file=cache_file,
        loader=_loader(rows, probe, calls1),
        probe_loader=lambda: probe,
    )
    asyncio.run(c1.ensure_built())
    assert calls1 == [1]
    assert c1._built is True
    assert c1._index is not None

    calls2: list = []
    c2 = BM25Corpus(
        cache_file=cache_file,
        loader=_loader(rows, probe, calls2),
        probe_loader=lambda: probe,
    )
    asyncio.run(c2.ensure_built())
    assert calls2 == []  # 探针一致 → 直接加载落盘索引，不重建
    assert c2._built is True
    assert c2._doc_ids == [1, 2]


def test_bm25_cache_rebuilds_on_probe_change(tmp_path):
    cache_file = str(tmp_path / "bm25.pkl")
    rows, probe = _rows()
    calls: list = []
    c1 = BM25Corpus(
        cache_file=cache_file,
        loader=_loader(rows, probe, calls),
        probe_loader=lambda: probe,
    )
    asyncio.run(c1.ensure_built())

    rows2 = [dict(rows[0], recipe_id=9, title="新菜")]
    probe2 = (1, 9, "2026-02-01T00:00:00")
    calls2: list = []
    c2 = BM25Corpus(
        cache_file=cache_file,
        loader=_loader(rows2, probe2, calls2),
        probe_loader=lambda: probe2,
    )
    asyncio.run(c2.ensure_built())
    assert calls2 == [1]  # 探针变化 → 全量重建并重新落盘
    assert c2._doc_ids == [9]


def test_bm25_cache_custom_loader_bypasses(tmp_path):
    rows, probe = _rows()
    calls: list = []
    corpus = BM25Corpus(
        loader=_loader(rows, probe, calls),
        probe_loader=lambda: probe,
    )
    assert corpus._use_cache is False  # 未显式传 cache_file → 不落盘
    asyncio.run(corpus.ensure_built())
    assert calls == [1]
