"""LangGraph 端到端：query 构造、retry_count 合并/上限、空结果、降级补全与并发。"""

import asyncio
import time

from app.config import Settings
from app.graph.linking import IngredientLinker
from app.graph.state import Recommendation, empty_state
from app.graph.workflow import build_graph
from app.retrieval.bm25 import BM25Corpus, load_recipe_rows
from app.retrieval.hybrid import HybridRetriever
from app.retrieval.ranking import EMPTY_RESULT_NOTICE, RankingService
from app.schemas.recommend import RecommendationSet
from app.vector_store import ChromaStore
from tests.conftest import FakeEmbeddings
from tests.helpers import FakeLLM, add_recipe, delete_recipe

URL = "https://test.flow/1"
TITLE = "流程测试土豆鸡蛋菜"
STEPS = [{"instruction": "土豆切块", "minutes": 5}]


def _isolated_corpus(urls: set[str]) -> BM25Corpus:
    all_rows, _probe = load_recipe_rows()
    rows = [r for r in all_rows if r["source_url"] in urls]
    probe = ("flow-test", len(rows), max((r["recipe_id"] for r in rows), default=0))
    return BM25Corpus(loader=lambda: (rows, probe))


def _seed_env(tmp_path):
    rid = add_recipe(
        TITLE, URL, ingredients=["土豆", "鸡蛋"], tags=["家常菜"], steps=STEPS
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
    return rid, service


def _patch_deps(monkeypatch, service, llm):
    monkeypatch.setattr("app.graph.nodes.get_ranking_service", lambda: service)
    monkeypatch.setattr(
        "app.graph.nodes.get_ingredient_linker",
        lambda: IngredientLinker(embeddings=None),
    )
    monkeypatch.setattr("app.graph.nodes.get_llm_provider", lambda: llm)


def _llm(rid, **kwargs):
    return FakeLLM(
        parse_items=[("土豆",), ("鸡蛋",)],
        recommendation_set=RecommendationSet(
            recommendations=[
                Recommendation(
                    recipe_id=rid,
                    title=TITLE,
                    match_score=0.9,
                    missing_ingredients=[],
                    steps=[{"instruction": "LLM 步骤"}],
                )
            ]
        ),
        **kwargs,
    )


def test_flow_end_to_end_query_and_retry_merge(tmp_path, monkeypatch):
    rid, service = _seed_env(tmp_path)
    llm = _llm(rid)
    _patch_deps(monkeypatch, service, llm)
    try:
        graph = build_graph()
        result = asyncio.run(
            graph.ainvoke(empty_state(ingredients=["土豆", "鸡蛋"], exclude_tags=[]))
        )
        assert result["query"] == "土豆 鸡蛋"  # filter 构造标准名 query
        assert result["ingredients"] == ["土豆", "鸡蛋"]
        assert result["retry_count"] == 0  # 成功路径保持 0
        assert result["degraded"] is False
        assert len(result["recommendations"]) == 1
        assert result["recommendations"][0].recipe_id == rid
    finally:
        delete_recipe(URL)


def test_flow_parse_retry_branch(tmp_path, monkeypatch):
    rid, service = _seed_env(tmp_path)
    llm = _llm(rid, fail_parse=1)
    _patch_deps(monkeypatch, service, llm)
    try:
        graph = build_graph()
        result = asyncio.run(
            graph.ainvoke(empty_state(ingredients=["土豆", "鸡蛋"]))
        )
        assert llm.parse_calls == 2  # 第 1 次失败 → 重试 1 次
        assert result["retry_count"] == 1  # 已消耗 1 次重试（合并保留）
        assert result["degraded"] is False
        assert len(result["recommendations"]) == 1
    finally:
        delete_recipe(URL)


def test_flow_parse_over_limit_degrades(tmp_path, monkeypatch):
    rid, service = _seed_env(tmp_path)
    llm = _llm(rid, fail_parse=2)
    _patch_deps(monkeypatch, service, llm)
    try:
        graph = build_graph()
        result = asyncio.run(
            graph.ainvoke(empty_state(ingredients=["土豆", "鸡蛋"]))
        )
        assert llm.parse_calls == 2
        assert result["retry_count"] == 2
        assert result["degraded"] is True
        assert result["notice"] == "未能识别食材，请补充描述"
        assert result["recommendations"] == []
    finally:
        delete_recipe(URL)


def test_flow_max_parse_retries_zero_degrades_immediately(tmp_path, monkeypatch):
    rid, service = _seed_env(tmp_path)
    llm = _llm(rid, fail_parse=1)
    _patch_deps(monkeypatch, service, llm)
    monkeypatch.setattr(
        "app.graph.nodes.get_settings",
        lambda: Settings(recommend_max_parse_retries=0),
    )
    monkeypatch.setattr(
        "app.graph.workflow.get_settings",
        lambda: Settings(recommend_max_parse_retries=0),
    )
    try:
        graph = build_graph()
        result = asyncio.run(
            graph.ainvoke(empty_state(ingredients=["土豆", "鸡蛋"]))
        )
        assert llm.parse_calls == 1
        assert result["retry_count"] == 1
        assert result["degraded"] is True
    finally:
        delete_recipe(URL)


def test_flow_no_candidates_returns_notice(tmp_path, monkeypatch):
    rid, service = _seed_env(tmp_path)
    llm = FakeLLM(parse_items=[("神秘食材",)])
    _patch_deps(monkeypatch, service, llm)
    try:
        graph = build_graph()
        result = asyncio.run(
            graph.ainvoke(empty_state(ingredients=["神秘食材"]))
        )
        assert result["candidates"] == []
        assert result["ranked"] == []
        assert result["recommendations"] == []
        assert result["notice"] == EMPTY_RESULT_NOTICE
        assert result["degraded"] is False
    finally:
        delete_recipe(URL)


def test_flow_generate_degrade_fills_steps(tmp_path, monkeypatch):
    rid, service = _seed_env(tmp_path)
    llm = FakeLLM(parse_items=[("土豆",), ("鸡蛋",)], fail_generate=True)
    _patch_deps(monkeypatch, service, llm)
    try:
        graph = build_graph()
        result = asyncio.run(
            graph.ainvoke(empty_state(ingredients=["土豆", "鸡蛋"]))
        )
        assert result["degraded"] is True
        assert result["notice"] == "AI 文案不可用，已展示菜谱原文"
        rec = result["recommendations"][0]
        assert rec.steps == STEPS
        assert rec.difficulty == 1
        assert rec.tips is None
    finally:
        delete_recipe(URL)


def test_flow_concurrent_requests_do_not_crash(tmp_path, monkeypatch):
    rid, service = _seed_env(tmp_path)
    llm = _llm(rid, latency=0.02)
    _patch_deps(monkeypatch, service, llm)
    try:
        graph = build_graph()

        async def one():
            return await graph.ainvoke(
                empty_state(ingredients=["土豆", "鸡蛋"])
            )

        async def run_five():
            return await asyncio.gather(*(one() for _ in range(5)))

        started = time.perf_counter()
        results = asyncio.run(run_five())
        elapsed = time.perf_counter() - started
        assert all(r["recommendations"] for r in results)
        assert llm.parse_calls == 5
        assert llm.generate_calls == 5
        assert elapsed < 5.0  # P95 门禁：5 并发 < 5s
    finally:
        delete_recipe(URL)
