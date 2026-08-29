"""HybridRetriever：向量聚合均值、四态降级、RRF 融合不变量与孤儿块。"""

import asyncio

import pytest

from app.config import Settings
from app.retrieval.errors import RetrievalUnavailableError
from app.retrieval.hybrid import HybridRetriever
from app.vector_store import ChromaStore
from tests.conftest import FakeEmbeddings
from tests.helpers import add_recipe, delete_recipe

K = 60
W = 0.5


def _chroma(tmp_path) -> ChromaStore:
    return ChromaStore(path=str(tmp_path / "chroma"))


def _upsert(store: ChromaStore, url: str, title: str, chunks: list[str], embeddings) -> None:
    ids = [f"{url}#{i}" for i in range(len(chunks))]
    metadatas = [
        {
            "source_url": url,
            "title": title,
            "site": "test",
            "chunk_index": i,
            "unit_type": "header",
        }
        for i in range(len(chunks))
    ]
    vectors = asyncio.run(embeddings.embed_texts(chunks))
    asyncio.run(
        store.upsert(ids=ids, documents=chunks, metadatas=metadatas, embeddings=vectors)
    )


def _settings(**kwargs) -> Settings:
    return Settings(**kwargs)


def test_fusion_invariant_same_rank_both_paths(tmp_path):
    url = "https://test.invariant/1"
    try:
        rid = add_recipe("罕见词xqz融合测试", url, ingredients=["土豆", "鸡蛋"])
        embeddings = FakeEmbeddings()
        chroma = _chroma(tmp_path)
        _upsert(chroma, url, "罕见词xqz融合测试", ["罕见词xqz融合测试"], embeddings)
        retriever = HybridRetriever(embeddings=embeddings, chroma=chroma)
        candidates = asyncio.run(retriever.retrieve("罕见词xqz", 5))
        assert candidates and candidates[0].recipe_id == rid
        # 单路项均为 w/(k+1)，两路同位次 → 融合分恰为两倍
        expected = 2 * W / (K + 1)
        assert abs(candidates[0].match_score - expected) < 1e-9
        assert candidates[0].degraded is False
    finally:
        delete_recipe(url)


def test_vector_aggregation_mean_dilutes_lucky_chunk(tmp_path):
    url_a = "https://test.agg/a"
    url_b = "https://test.agg/b"
    try:
        add_recipe("异菜甲", url_a, ingredients=["土豆"])
        add_recipe("异菜乙", url_b, ingredients=["鸡蛋"])
        embeddings = FakeEmbeddings()
        chroma = _chroma(tmp_path)
        _upsert(
            chroma,
            url_a,
            "异菜甲",
            # 一块强命中 + 两块弱命中（均过 0.5 距离阈值，但被均值稀释）
            ["罕见词xqz甲核心词", "罕见见词甲乙", "罕见见词丙丁"],
            embeddings,
        )
        _upsert(
            chroma,
            url_b,
            "异菜乙",
            ["罕见词xqz乙一", "罕见词xqz乙二", "罕见词xqz乙三"],
            embeddings,
        )
        retriever = HybridRetriever(embeddings=embeddings, chroma=chroma)
        candidates = asyncio.run(retriever.retrieve("罕见词xqz", 5))
        assert len(candidates) == 2
        # 多块佐证的乙 > 单块幸运的甲（均值稀释）
        assert candidates[0].title == "异菜乙"
        assert candidates[0].match_score > candidates[1].match_score
        assert candidates[1].title == "异菜甲"
        assert candidates[0].degraded is False
    finally:
        delete_recipe(url_a)
        delete_recipe(url_b)


def test_vector_empty_raw_hits_not_degraded(tmp_path):
    url = "https://test.empty/1"
    try:
        add_recipe("罕见词xqz空结果", url, ingredients=["土豆"])
        embeddings = FakeEmbeddings()
        chroma = _chroma(tmp_path)
        _upsert(chroma, url, "罕见词xqz空结果", ["罕见词xqz空结果"], embeddings)

        class _EmptyHitsChroma(ChromaStore):
            def count(self) -> int:
                return 1

            async def query(self, query_embeddings, n_results, where=None):
                return []

        # 非空集合下 Chroma 总会返回 top_n，raw hits=0 只能由打桩模拟
        retriever = HybridRetriever(embeddings=embeddings, chroma=_EmptyHitsChroma(path=str(tmp_path / "chroma")))
        candidates = asyncio.run(retriever.retrieve("完全无关zzyy", 5))
        assert candidates == []
        assert retriever.last_notice is None
    finally:
        delete_recipe(url)


def test_all_orphan_degrades(tmp_path):
    embeddings = FakeEmbeddings()
    chroma = _chroma(tmp_path)
    _upsert(chroma, "https://orphan.example/1", "孤儿菜", ["罕见词xqz孤儿块"], embeddings)
    retriever = HybridRetriever(embeddings=embeddings, chroma=chroma)
    candidates = asyncio.run(retriever.retrieve("罕见词xqz", 5))
    assert retriever.last_notice is not None and "孤儿" in retriever.last_notice
    assert candidates == [] or all(c.degraded for c in candidates)


def test_partial_orphan_keeps_valid(tmp_path):
    url = "https://test.partial/1"
    try:
        rid = add_recipe("罕见词xqz部分孤儿", url, ingredients=["土豆"])
        embeddings = FakeEmbeddings()
        chroma = _chroma(tmp_path)
        _upsert(chroma, url, "罕见词xqz部分孤儿", ["罕见词xqz有效块"], embeddings)
        _upsert(
            chroma,
            "https://orphan.example/2",
            "孤儿菜二",
            ["罕见词xqz孤儿块二"],
            embeddings,
        )
        retriever = HybridRetriever(embeddings=embeddings, chroma=chroma)
        candidates = asyncio.run(retriever.retrieve("罕见词xqz", 5))
        assert any(c.recipe_id == rid for c in candidates)
        assert retriever.last_notice is None
    finally:
        delete_recipe(url)


def test_embedding_failure_degrades(tmp_path):
    url = "https://test.embedfail/1"
    try:
        add_recipe("罕见词xqz嵌入故障", url, ingredients=["土豆"])
        chroma = _chroma(tmp_path)
        _upsert(chroma, url, "罕见词xqz嵌入故障", ["罕见词xqz嵌入故障"], FakeEmbeddings())
        retriever = HybridRetriever(
            embeddings=FakeEmbeddings(fail=True), chroma=chroma
        )
        candidates = asyncio.run(retriever.retrieve("罕见词xqz", 5))
        assert candidates and candidates[0].degraded is True
        assert retriever.last_notice is not None and "向量检索不可用" in retriever.last_notice
    finally:
        delete_recipe(url)


def test_no_key_degrades(tmp_path):
    url = "https://test.nokey/1"
    try:
        add_recipe("罕见词xqz无key", url, ingredients=["土豆"])
        chroma = _chroma(tmp_path)
        _upsert(chroma, url, "罕见词xqz无key", ["罕见词xqz无key"], FakeEmbeddings())
        retriever = HybridRetriever(
            _settings(embedding_api_key=None), embeddings=None, chroma=chroma
        )
        candidates = asyncio.run(retriever.retrieve("罕见词xqz", 5))
        assert candidates and candidates[0].degraded is True
        assert retriever.last_notice is not None and "EMBEDDING_API_KEY" in retriever.last_notice
    finally:
        delete_recipe(url)


def test_chroma_empty_degrades(tmp_path):
    url = "https://test.emptycoll/1"
    try:
        add_recipe("罕见词xqz空集合", url, ingredients=["土豆"])
        retriever = HybridRetriever(
            embeddings=FakeEmbeddings(), chroma=_chroma(tmp_path)
        )
        candidates = asyncio.run(retriever.retrieve("罕见词xqz", 5))
        assert candidates and candidates[0].degraded is True
        assert retriever.last_notice is not None and "Chroma 集合为空" in retriever.last_notice
    finally:
        delete_recipe(url)


def test_vector_lookup_mysql_failure_raises(tmp_path, monkeypatch):
    url = "https://test.mysqlfail/1"
    try:
        add_recipe("罕见词xqz反查失败", url, ingredients=["土豆"])
        embeddings = FakeEmbeddings()
        chroma = _chroma(tmp_path)
        _upsert(chroma, url, "罕见词xqz反查失败", ["罕见词xqz反查失败"], embeddings)
        retriever = HybridRetriever(embeddings=embeddings, chroma=chroma)

        def boom(urls):
            raise RuntimeError("mysql down")

        monkeypatch.setattr(retriever, "_lookup_recipes", boom)
        with pytest.raises(RetrievalUnavailableError):
            asyncio.run(retriever.retrieve("罕见词xqz", 5))
    finally:
        delete_recipe(url)


def test_vector_disabled_flag(tmp_path):
    retriever = HybridRetriever(
        embeddings=FakeEmbeddings(), chroma=_chroma(tmp_path), enable_vector=False
    )
    candidates = asyncio.run(retriever.retrieve("罕见词xqz", 5))
    assert retriever.last_notice is not None and "向量路未启用" in retriever.last_notice
    assert candidates == [] or all(c.degraded for c in candidates)
