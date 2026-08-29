"""LangGraph retrieve/rank 节点：查询源、空查询、Top-K 与真实流程。"""

import asyncio

from app.core.retriever import RecipeCandidate
from app.graph.nodes import rank_node, retrieve_node
from app.graph.state import CookState, empty_state
from app.retrieval.errors import RetrievalUnavailableError
from app.retrieval.hybrid import HybridRetriever
from app.retrieval.ranking import RankResult, RankingService
from app.vector_store import ChromaStore
from tests.conftest import FakeEmbeddings
from tests.helpers import add_recipe, delete_recipe


class FakeRanking:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or RankResult([], False, None)

    async def rank(self, query, available_ingredients=None, exclude_tags=None, top_k=None):
        self.calls.append((query, available_ingredients, exclude_tags, top_k))
        return self.result


def test_retrieve_uses_state_query(monkeypatch):
    fake = FakeRanking()
    monkeypatch.setattr("app.graph.nodes.get_ranking_service", lambda: fake)
    state = empty_state(query="土豆 鸡蛋", ingredients=["土豆"], exclude_tags=["辣"])
    result = asyncio.run(retrieve_node(state))
    assert fake.calls[0][0] == "土豆 鸡蛋"
    assert fake.calls[0][1] == ["土豆"]
    assert fake.calls[0][2] == ["辣"]
    assert result["notice"] is None


def test_retrieve_empty_query_no_fallback(monkeypatch):
    fake = FakeRanking()
    monkeypatch.setattr("app.graph.nodes.get_ranking_service", lambda: fake)
    result = asyncio.run(retrieve_node(empty_state(query="")))
    assert result["candidates"] == []
    assert result["notice"] == "缺少查询文本"
    assert fake.calls == []


def test_retrieve_unavailable_degrades(monkeypatch):
    class Boom(FakeRanking):
        async def rank(self, query, available_ingredients=None, exclude_tags=None, top_k=None):
            raise RetrievalUnavailableError("mysql down")

    monkeypatch.setattr("app.graph.nodes.get_ranking_service", lambda: Boom())
    result = asyncio.run(retrieve_node(empty_state(query="土豆")))
    assert result["candidates"] == []
    assert result["degraded"] is True
    assert result["notice"] == "检索服务暂不可用，请稍后重试"


def test_rank_takes_top5():
    candidates = [
        RecipeCandidate(recipe_id=i, title=f"菜{i}", match_score=float(10 - i))
        for i in range(1, 8)
    ]
    state = empty_state(query="土豆")
    state.candidates = candidates
    result = asyncio.run(rank_node(state))
    assert [c.recipe_id for c in result["ranked"]] == [1, 2, 3, 4, 5]


def test_retrieve_rank_nodes_integration(tmp_path, monkeypatch):
    url = "https://test.nodes/1"
    try:
        add_recipe("节点测试土豆鸡蛋菜甲", url, ingredients=["土豆", "鸡蛋"])
        embeddings = FakeEmbeddings()
        chroma = ChromaStore(path=str(tmp_path / "chroma"))
        docs = ["节点测试土豆鸡蛋菜甲 用料"]
        vectors = asyncio.run(embeddings.embed_texts(docs))
        asyncio.run(
            chroma.upsert(
                ids=[f"{url}#0"],
                documents=docs,
                metadatas=[
                    {
                        "source_url": url,
                        "title": "节点测试土豆鸡蛋菜甲",
                        "site": "test",
                        "chunk_index": 0,
                        "unit_type": "header",
                    }
                ],
                embeddings=vectors,
            )
        )
        service = RankingService(
            retriever=HybridRetriever(embeddings=embeddings, chroma=chroma)
        )
        monkeypatch.setattr("app.graph.nodes.get_ranking_service", lambda: service)
        state = empty_state(query="节点测试土豆鸡蛋菜", ingredients=["土豆", "鸡蛋"])
        retrieved = asyncio.run(retrieve_node(state))
        ranked = asyncio.run(rank_node(CookState(**retrieved)))
        assert retrieved["candidates"]
        assert ranked["ranked"] == retrieved["candidates"][:5]
        scores = [c.match_score for c in ranked["ranked"]]
        assert scores == sorted(scores, reverse=True)
    finally:
        delete_recipe(url)
