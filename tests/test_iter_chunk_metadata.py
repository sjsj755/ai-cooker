"""孤儿块分页：iterator 全量不重不漏、空集合、单页失败重试、连续失败中止。

中止策略：单页读取重试后仍失败 → FallbackError（含 offset/batch/max_attempts），
脚本退出码 3、ERROR 日志、不删除任何数据；dry-run 同样中止。
"""

import asyncio
import logging

import pytest

from app.core.fallback import FallbackError
from app.vector_store import ChromaStore
import scripts.cleanup_orphan_chunks as mod


class FakeCollection:
    """模拟 Chroma collection.get(where/limit/offset/include)，可注入分页失败。"""

    def __init__(self, metas: list[dict], fail_pages: dict[int, int] | None = None):
        self.metas = metas
        self.fail_pages = dict(fail_pages or {})
        self.get_calls: list[int] = []
        self.deleted: list[tuple] = []

    def get(self, *, where=None, limit=None, offset=0, include=None):
        self.get_calls.append(offset)
        if self.fail_pages.get(offset, 0) > 0:
            self.fail_pages[offset] -= 1
            raise RuntimeError("chroma down")
        return {"metadatas": self.metas[offset : offset + limit]}

    def delete(self, *, where=None, ids=None):
        self.deleted.append((where, ids))


def _metas(n: int) -> list[dict]:
    return [
        {"source_url": f"https://page.test/{i}", "chunk_index": i}
        for i in range(n)
    ]


def _store(tmp_path, collection: FakeCollection) -> ChromaStore:
    store = ChromaStore(path=str(tmp_path / "chroma"))
    store._collection = collection
    return store


async def _collect(store: ChromaStore, **kwargs):
    return [m async for m in store.iter_chunk_metadata(**kwargs)]


def test_iter_full_2050_no_dup_no_missing(tmp_path):
    metas = _metas(2050)
    collection = FakeCollection(metas)
    store = _store(tmp_path, collection)
    rows = asyncio.run(_collect(store, batch_size=1000, max_attempts=3))
    assert len(rows) == 2050
    assert len({m["chunk_index"] for m in rows}) == 2050
    assert collection.get_calls == [0, 1000, 2000]


def test_empty_collection_normal(tmp_path):
    collection = FakeCollection([])
    store = _store(tmp_path, collection)
    assert asyncio.run(_collect(store)) == []


def test_page_failure_retries_then_success(tmp_path):
    metas = _metas(2500)
    collection = FakeCollection(metas, fail_pages={1000: 2})
    store = _store(tmp_path, collection)
    rows = asyncio.run(_collect(store, batch_size=1000, max_attempts=3))
    assert len(rows) == 2500
    assert collection.get_calls.count(1000) == 3  # 失败 2 次 + 成功 1 次


def test_page_failure_aborts_with_context(tmp_path):
    metas = _metas(2500)
    collection = FakeCollection(metas, fail_pages={2000: 99})
    store = _store(tmp_path, collection)
    with pytest.raises(FallbackError) as exc:
        asyncio.run(_collect(store, batch_size=1000, max_attempts=3))
    assert "offset=2000" in str(exc.value)
    assert "batch=1000" in str(exc.value)
    assert "max_attempts=3" in str(exc.value)
    assert collection.deleted == []


def test_script_aborts_exit_3_without_delete(tmp_path, monkeypatch):
    metas = _metas(2500)
    collection = FakeCollection(metas, fail_pages={1000: 99})
    store = _store(tmp_path, collection)
    monkeypatch.setattr(mod, "ChromaStore", lambda settings: store)
    captured: dict = {}
    monkeypatch.setattr(
        mod,
        "log_event",
        lambda logger, level, event, **fields: captured.update(
            level=level, event=event, fields=fields
        ),
    )
    rc = mod.main(["--dry-run", "--max-retries", "1"])
    assert rc == 3
    assert collection.get_calls.count(1000) == 1  # --max-retries 1 生效
    assert collection.deleted == []
    assert captured["event"] == "cleanup.orphan.scan_failed"
    assert captured["level"] == logging.ERROR
    assert "offset=1000" in captured["fields"]["error"]


def test_script_rejects_invalid_max_retries(tmp_path, monkeypatch):
    assert mod.main(["--max-retries", "0"]) == 2
