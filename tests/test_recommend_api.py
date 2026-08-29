"""POST /api/recipes/recommend：P3 200 完整工作流、降级补全、空输入 400、503。"""

import asyncio

from app.config import Settings
from app.graph.linking import IngredientLinker
from app.graph.state import Recommendation
from app.main import app
from app.retrieval.bm25 import BM25Corpus, load_recipe_rows
from app.retrieval.hybrid import HybridRetriever
from app.retrieval.ranking import RankingService
from app.schemas.recommend import RecommendationSet
from app.vector_store import ChromaStore
from tests.conftest import FakeEmbeddings
from tests.helpers import FakeLLM, add_recipe, delete_recipe

URL = "https://test.recommend/1"
TITLE = "推荐接口测试土豆鸡蛋菜"
STEPS = [{"instruction": "土豆切块", "minutes": 5}, {"instruction": "与鸡蛋同炒", "minutes": 10}]


def _isolated_corpus(urls: set[str]) -> BM25Corpus:
    all_rows, _probe = load_recipe_rows()
    rows = [r for r in all_rows if r["source_url"] in urls]
    probe = ("recommend-test", len(rows), max((r["recipe_id"] for r in rows), default=0))
    return BM25Corpus(loader=lambda: (rows, probe))


def _seed_env(tmp_path):
    rid = add_recipe(
        TITLE,
        URL,
        ingredients=["土豆", "鸡蛋"],
        tags=["家常菜"],
        steps=STEPS,
    )
    embeddings = FakeEmbeddings()
    chroma = ChromaStore(path=str(tmp_path / "chroma"))
    docs = ["土豆 鸡蛋 用料与步骤"]
    vectors = asyncio.run(embeddings.embed_texts(docs))
    asyncio.run(
        chroma.upsert(
            ids=[f"{URL}#0"],
            documents=docs,
            metadatas=[
                {
                    "source_url": URL,
                    "title": TITLE,
                    "site": "test",
                    "chunk_index": 0,
                    "unit_type": "header",
                }
            ],
            embeddings=vectors,
        )
    )
    service = RankingService(
        retriever=HybridRetriever(
            Settings(retrieval_vector_max_distance=0.5),
            embeddings=FakeEmbeddings(),
            chroma=chroma,
            corpus=_isolated_corpus({URL}),
        )
    )
    return rid, service, chroma


def _patch_deps(monkeypatch, service, llm=None, tmp_path=None):
    monkeypatch.setattr("app.graph.nodes.get_ranking_service", lambda: service)
    monkeypatch.setattr(
        "app.graph.nodes.get_ingredient_linker",
        lambda: IngredientLinker(embeddings=None),
    )
    monkeypatch.setattr("app.graph.nodes.get_llm_provider", lambda: llm)


def test_recommend_200_with_mock_llm(client, tmp_path, monkeypatch):
    rid, service, _chroma = _seed_env(tmp_path)
    llm = FakeLLM(
        parse_items=[("土豆",), ("鸡蛋",)],
        recommendation_set=RecommendationSet(
            recommendations=[
                Recommendation(
                    recipe_id=rid,
                    title=TITLE,
                    match_score=0.9,
                    missing_ingredients=[],
                    steps=[{"instruction": "LLM 步骤"}],
                    tips="少放盐",
                )
            ]
        ),
    )
    _patch_deps(monkeypatch, service, llm)
    try:
        resp = client.post(
            "/api/recipes/recommend",
            json={"ingredients": ["土豆", "鸡蛋"], "exclude_tags": []},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["degraded"] is False
        assert body["notice"] is None
        assert len(body["recipes"]) == 1
        rec = body["recipes"][0]
        assert rec["recipe_id"] == rid
        assert rec["title"] == TITLE
        assert rec["steps"] == [{"instruction": "LLM 步骤"}]
        assert rec["tips"] == "少放盐"
        assert set(rec) == {
            "recipe_id",
            "title",
            "match_score",
            "missing_ingredients",
            "difficulty",
            "cook_time_minutes",
            "steps",
            "tips",
        }
    finally:
        delete_recipe(URL)


def test_recommend_degrade_fills_steps_from_mysql(client, tmp_path, monkeypatch):
    rid, service, _chroma = _seed_env(tmp_path)
    llm = FakeLLM(parse_items=[("土豆",), ("鸡蛋",)], fail_generate=True)
    _patch_deps(monkeypatch, service, llm)
    try:
        resp = client.post(
            "/api/recipes/recommend",
            json={"ingredients": ["土豆", "鸡蛋"], "exclude_tags": []},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["degraded"] is True
        assert body["notice"] == "AI 文案不可用，已展示菜谱原文"
        assert len(body["recipes"]) == 1
        rec = body["recipes"][0]
        assert rec["recipe_id"] == rid
        assert rec["steps"] == STEPS
        assert rec["difficulty"] == 1
        assert rec["cook_time_minutes"] == 20
        assert rec["tips"] is None
    finally:
        delete_recipe(URL)


def test_recommend_no_llm_key_degrades(client, tmp_path, monkeypatch):
    rid, service, _chroma = _seed_env(tmp_path)
    _patch_deps(monkeypatch, service, llm=None)
    try:
        resp = client.post(
            "/api/recipes/recommend",
            json={"ingredients": ["土豆", "鸡蛋"], "exclude_tags": []},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["recipes"] == []
        assert body["degraded"] is True
        assert body["notice"] == "未能识别食材，请补充描述"
    finally:
        delete_recipe(URL)


def test_recommend_empty_ingredients_400(client):
    resp = client.post(
        "/api/recipes/recommend",
        json={"ingredients": ["  ", " "], "exclude_tags": []},
    )
    assert resp.status_code == 400


def test_recommend_degrade_mysql_failure_503(client, tmp_path, monkeypatch):
    rid, service, _chroma = _seed_env(tmp_path)
    llm = FakeLLM(parse_items=[("土豆",), ("鸡蛋",)], fail_generate=True)
    _patch_deps(monkeypatch, service, llm)

    def boom():
        raise RuntimeError("mysql down")

    monkeypatch.setattr("app.graph.nodes.SessionLocal", boom)
    try:
        resp = client.post(
            "/api/recipes/recommend",
            json={"ingredients": ["土豆", "鸡蛋"], "exclude_tags": []},
        )
        assert resp.status_code == 503
    finally:
        delete_recipe(URL)
