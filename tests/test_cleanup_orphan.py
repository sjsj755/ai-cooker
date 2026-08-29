"""孤块清理：--dry-run 无副作用，真实删除仅移除孤儿块。"""

import asyncio

from app.config import Settings
from app.vector_store import ChromaStore
from tests.conftest import FakeEmbeddings
from tests.helpers import add_recipe, delete_recipe
import scripts.cleanup_orphan_chunks as mod


def test_cleanup_dry_run_and_delete(tmp_path, monkeypatch):
    url_valid = "https://test.cleanup/1"
    url_orphan = "https://orphan.cleanup/1"
    try:
        add_recipe("清理测试菜", url_valid, ingredients=["土豆"])
        store = ChromaStore(path=str(tmp_path / "chroma"))
        embeddings = FakeEmbeddings()
        docs = ["有效块", "孤儿块"]
        ids = ["valid#0", "orphan#0"]
        metas = [
            {
                "source_url": url_valid,
                "title": "清理测试菜",
                "site": "test",
                "chunk_index": 0,
                "unit_type": "header",
            },
            {
                "source_url": url_orphan,
                "title": "孤儿",
                "site": "test",
                "chunk_index": 0,
                "unit_type": "header",
            },
        ]
        vectors = asyncio.run(embeddings.embed_texts(docs))
        asyncio.run(
            store.upsert(ids=ids, documents=docs, metadatas=metas, embeddings=vectors)
        )
        monkeypatch.setattr(
            mod, "get_settings", lambda: Settings(chroma_dir=str(tmp_path / "chroma"))
        )

        assert mod.main(["--dry-run"]) == 0
        assert store.count() == 2

        assert mod.main([]) == 0
        assert store.count() == 1
        remaining = asyncio.run(store.get_chunk_metadata(None))
        assert remaining[0]["source_url"] == url_valid
    finally:
        delete_recipe(url_valid)
