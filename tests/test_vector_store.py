"""Chroma 向量库：幂等 upsert、count、维度冲突报错。"""

import asyncio

import pytest

from app.vector_store import ChromaDimensionError, ChromaStore


def _upsert(store: ChromaStore, ids, docs, metas, vecs) -> None:
    asyncio.run(store.upsert(ids=ids, documents=docs, metadatas=metas, embeddings=vecs))


def _metas(n: int) -> list[dict]:
    return [
        {"source_url": "https://x/1", "title": "t", "site": "xiachufang", "chunk_index": i}
        for i in range(n)
    ]


def test_upsert_idempotent(tmp_path):
    store = ChromaStore(path=str(tmp_path), collection="recipe_docs")
    _upsert(
        store,
        ["a#0", "a#1"],
        ["文档一", "文档二"],
        _metas(2),
        [[1.0, 0.0], [0.0, 1.0]],
    )
    assert store.count() == 2
    _upsert(
        store,
        ["a#0", "a#1"],
        ["文档一-新", "文档二-新"],
        _metas(2),
        [[1.0, 0.0], [0.0, 1.0]],
    )
    assert store.count() == 2


def test_empty_upsert_noop(tmp_path):
    store = ChromaStore(path=str(tmp_path))
    _upsert(store, [], [], [], [])
    assert store.count() == 0


def test_dimension_conflict_raises(tmp_path):
    store = ChromaStore(path=str(tmp_path))
    _upsert(store, ["a#0"], ["x"], _metas(1), [[1.0, 0.0]])
    with pytest.raises(ChromaDimensionError):
        _upsert(store, ["a#0"], ["x"], _metas(1), [[1.0, 0.0, 0.0]])


def test_heartbeat(tmp_path):
    store = ChromaStore(path=str(tmp_path))
    assert store.heartbeat() > 0


def test_delete_where_removes_all_matching(tmp_path):
    store = ChromaStore(path=str(tmp_path))
    _upsert(
        store,
        ["x#0", "x#1"],
        ["a", "b"],
        _metas(2),
        [[1.0, 0.0], [0.0, 1.0]],
    )
    asyncio.run(store.delete_where({"source_url": "https://x/1"}))
    assert store.count() == 0
    asyncio.run(store.delete_where({"source_url": "https://x/1"}))  # 幂等


def test_get_chunk_metadata(tmp_path):
    store = ChromaStore(path=str(tmp_path))
    metas = [
        {
            "source_url": "https://x/1",
            "title": "t",
            "site": "xiachufang",
            "chunk_index": 0,
            "unit_type": "steps",
            "step_start": 1,
            "step_end": 2,
        }
    ]
    _upsert(store, ["a#0"], ["x"], metas, [[1.0, 0.0]])
    out = asyncio.run(store.get_chunk_metadata({"source_url": "https://x/1"}))
    assert out == metas
