"""GET /api/recipes/search：结构、参数校验、空结果、503 与路由不冲突。"""

import asyncio

import pytest

from app.api.deps import get_ranking_service
from app.config import Settings
from app.main import app
from app.retrieval.bm25 import BM25Corpus, load_recipe_rows
from app.retrieval.errors import RetrievalUnavailableError
from app.retrieval.hybrid import HybridRetriever
from app.retrieval.ranking import EMPTY_RESULT_NOTICE, RankingService
from app.vector_store import ChromaStore
from tests.conftest import FakeEmbeddings
from tests.helpers import add_recipe, delete_recipe

URL = "https://test.search/1"
UNIQUE = "接口测试土豆鸡蛋"


def _service(tmp_path) -> RankingService:
    embeddings = FakeEmbeddings()
    chroma = ChromaStore(path=str(tmp_path / "chroma"))
    return RankingService(retriever=HybridRetriever(embeddings=embeddings, chroma=chroma))


def _seed_chroma(tmp_path, url: str, title: str) -> ChromaStore:
    embeddings = FakeEmbeddings()
    chroma = ChromaStore(path=str(tmp_path / "chroma"))
    ids = [f"{url}#0"]
    docs = [f"{title} 用料与步骤"]
    metas = [
        {
            "source_url": url,
            "title": title,
            "site": "test",
            "chunk_index": 0,
            "unit_type": "header",
        }
    ]
    vectors = asyncio.run(embeddings.embed_texts(docs))
    asyncio.run(chroma.upsert(ids=ids, documents=docs, metadatas=metas, embeddings=vectors))
    return chroma


def _isolated_corpus(urls: set[str]) -> BM25Corpus:
    """隔离语料：只含本测试插入的菜谱，避免共享测试库污染。"""
    all_rows, _probe = load_recipe_rows()
    rows = [r for r in all_rows if r["source_url"] in urls]
    probe = ("search-test", len(rows), max((r["recipe_id"] for r in rows), default=0))
    return BM25Corpus(loader=lambda: (rows, probe))


@pytest.fixture()
def search_env(tmp_path):
    rid = add_recipe(UNIQUE, URL, ingredients=["土豆", "鸡蛋"], tags=["家常菜"])
    chroma = _seed_chroma(tmp_path, URL, UNIQUE)
    service = RankingService(
        retriever=HybridRetriever(
            Settings(retrieval_vector_max_distance=0.5),
            embeddings=FakeEmbeddings(),
            chroma=chroma,
            corpus=_isolated_corpus({URL}),
        )
    )
    app.dependency_overrides[get_ranking_service] = lambda: service
    yield rid
    app.dependency_overrides.pop(get_ranking_service, None)
    delete_recipe(URL)


def test_search_returns_results(client, search_env):
    resp = client.get(
        "/api/recipes/search",
        params={"q": UNIQUE, "ingredients": "土豆,鸡蛋"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["recipes"]
    first = payload["recipes"][0]
    assert first["recipe_id"] == search_env
    assert UNIQUE in first["title"]
    assert set(first) == {"recipe_id", "title", "match_score", "missing_ingredients"}
    assert payload["degraded"] is False


def test_search_with_ingredients_full_coverage(client, search_env):
    resp = client.get(
        "/api/recipes/search",
        params={"q": UNIQUE, "ingredients": "土豆,鸡蛋"},
    )
    assert resp.status_code == 200
    first = resp.json()["recipes"][0]
    assert first["missing_ingredients"] == []


def test_search_exclude_tags(client, search_env):
    resp = client.get(
        "/api/recipes/search",
        params={"q": UNIQUE, "exclude_tags": "家常菜"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["recipes"] == []
    assert payload["notice"] == EMPTY_RESULT_NOTICE


@pytest.mark.parametrize(
    "params",
    [
        {"q": "   "},
        {"q": "长" * 201},
        {"q": "含\x00控制符"},
        {"q": UNIQUE, "ingredients": ",".join(["长"] * 31)},
        {"q": UNIQUE, "ingredients": "长" * 51},
        {"q": UNIQUE, "exclude_tags": ",".join(["长"] * 21)},
        {"q": UNIQUE, "limit": 51},
        {"q": UNIQUE, "limit": 0},
    ],
)
def test_search_invalid_input_400(client, search_env, params):
    resp = client.get("/api/recipes/search", params=params)
    assert resp.status_code == 400


def test_search_empty_result_notice(client, search_env):
    resp = client.get("/api/recipes/search", params={"q": "完全不存在的菜谱zzz"})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["recipes"] == []
    assert payload["notice"] == EMPTY_RESULT_NOTICE


def test_search_mysql_failure_503(client, tmp_path):
    class _FailRetriever:
        last_notice = None

        async def retrieve(self, query, top_k):
            raise RetrievalUnavailableError("mysql down")

    service = RankingService(retriever=_FailRetriever())
    app.dependency_overrides[get_ranking_service] = lambda: service
    try:
        resp = client.get("/api/recipes/search", params={"q": UNIQUE})
        assert resp.status_code == 503
    finally:
        app.dependency_overrides.pop(get_ranking_service, None)


def test_search_route_not_shadowed_by_detail(client, search_env):
    detail = client.get(f"/api/recipes/{search_env}")
    assert detail.status_code == 200
    search = client.get("/api/recipes/search", params={"q": UNIQUE})
    assert search.status_code == 200
