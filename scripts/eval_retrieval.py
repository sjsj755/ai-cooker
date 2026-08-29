"""检索评测：50 条用例 recall@5 / coverage，单路 vs 混合，性能基线。

用法：
    uv run python scripts/seed_synthetic_recipes.py --count 50
    uv run python scripts/eval_retrieval.py                       # 真实/BM25 评测
    uv run python scripts/eval_retrieval.py --fake-vector         # 离线混合对比
    uv run python scripts/eval_retrieval.py --bench-rows 1000     # 构建/查询耗时
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import math
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.core.crawler import CrawledIngredient, CrawledRecipe  # noqa: E402
from app.core.embeddings import EmbeddingProvider  # noqa: E402
from app.core.langsmith_trace import maybe_trace  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.ingestion.text_builder import chunk_recipe  # noqa: E402
from app.retrieval.bm25 import BM25Corpus, build_text, load_recipe_rows, tokenize  # noqa: E402
from app.retrieval.hybrid import HybridRetriever  # noqa: E402
from app.vector_store import ChromaStore  # noqa: E402
from scripts.seed_synthetic_recipes import MAIN, SECONDARY, STYLES, seed  # noqa: E402


class HashEmbeddings(EmbeddingProvider):
    """确定性伪嵌入：按 bigram 词袋哈希（md5，跨进程稳定），供离线混合评测。"""

    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in tokenize(text):
            digest = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
            vec[digest % self.dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


def build_cases(count: int = 50) -> list[tuple[str, str, list[str]]]:
    cases: list[tuple[str, str, list[str]]] = []
    for i in range(count):
        main = MAIN[i % len(MAIN)]
        style = STYLES[(i * 7) % len(STYLES)]
        if i % 3 == 0:
            query, keywords = f"{main} {style}", [main, style]
        elif i % 3 == 1:
            sec = SECONDARY[(i * 5) % len(SECONDARY)]
            if sec == main:
                sec = SECONDARY[(i * 5 + 1) % len(SECONDARY)]
            query, keywords = f"{main} {sec}", [main, sec]
        else:
            query, keywords = main, [main]
        cases.append((f"case_{i:02d}", query, keywords))
    return cases


def evaluate(
    retriever: HybridRetriever,
    cases: list[tuple[str, str, list[str]]],
    texts: dict[int, str],
) -> tuple[float, float, int]:
    recall_sum = 0.0
    coverage = 0.0
    used = 0
    for _name, query, keywords in cases:
        relevant = {
            rid for rid, text in texts.items() if all(kw in text for kw in keywords)
        }
        if not relevant:
            continue
        candidates = asyncio.run(retriever.retrieve(query, 5))
        top_ids = {c.recipe_id for c in candidates}
        hit = len(relevant & top_ids)
        recall_sum += hit / len(relevant)
        coverage += 1.0 if hit else 0.0
        used += 1
    return (recall_sum / used if used else 0.0), (coverage / used if used else 0.0), used


def rows_to_recipes(rows: list[dict]) -> list[CrawledRecipe]:
    recipes: list[CrawledRecipe] = []
    for row in rows:
        recipes.append(
            CrawledRecipe(
                title=row["title"],
                source_url=row["source_url"],
                description=row["description"],
                ingredients=[
                    CrawledIngredient(name=item["name"]) for item in row["ingredients"]
                ],
                seasonings=[
                    CrawledIngredient(name=item["name"], is_essential=False)
                    for item in row["seasonings"]
                ],
                tags=row["tags"],
                steps=[],
            )
        )
    return recipes


def build_fake_vector_store(
    rows: list[dict], embeddings: HashEmbeddings | None = None
) -> tuple[ChromaStore, HashEmbeddings]:
    tmp_dir = tempfile.mkdtemp(prefix="eval-chroma-")
    store = ChromaStore(path=str(Path(tmp_dir) / "chroma"))
    embeddings = embeddings or HashEmbeddings()
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []
    for recipe in rows_to_recipes(rows):
        chunks = chunk_recipe(recipe)
        for i, chunk in enumerate(chunks):
            ids.append(f"eval#{recipe.source_url}#{i}")
            documents.append(chunk.text)
            meta = {
                "source_url": recipe.source_url,
                "title": recipe.title,
                "site": "synthetic",
                "chunk_index": i,
                "unit_type": chunk.unit_type,
            }
            if chunk.step_start is not None:
                meta["step_start"] = chunk.step_start
                meta["step_end"] = chunk.step_end
            metadatas.append(meta)
    if ids:
        vectors = asyncio.run(embeddings.embed_texts(documents))
        asyncio.run(
            store.upsert(
                ids=ids, documents=documents, metadatas=metadatas, embeddings=vectors
            )
        )
    return store, embeddings


def bench_main(bench_rows: int) -> int:
    """1k/5k 语料基线：仅记录 BM25 构建与查询耗时，不跑 recall 门禁与向量路。"""
    settings = get_settings()
    seed(bench_rows)
    rows, _probe = load_recipe_rows()
    t0 = time.perf_counter()
    corpus = BM25Corpus(settings)
    asyncio.run(corpus.ensure_built())
    build_ms = (time.perf_counter() - t0) * 1000
    cases = build_cases(50)
    latencies = []
    for q in [q for _, q, _ in cases[:20]]:
        t1 = time.perf_counter()
        asyncio.run(corpus.search(q, 10))
        latencies.append((time.perf_counter() - t1) * 1000)
    latencies.sort()
    p95 = latencies[int(len(latencies) * 0.95) - 1] if latencies else 0.0
    print(
        f"[bench] 语料 {len(rows)} 条 构建={build_ms:.1f}ms  "
        f"查询P95={p95:.2f}ms  avg={statistics.mean(latencies):.2f}ms"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检索评测与性能基线")
    parser.add_argument("--synthetic-count", type=int, default=50, help="评测前确保存在的合成菜谱数")
    parser.add_argument("--fake-vector", action="store_true", help="用伪嵌入+临时 Chroma 做离线混合对比")
    parser.add_argument("--bench-rows", type=int, default=0, help="额外写入 N 条合成菜谱并计时")
    parser.add_argument("--min-recall", type=float, default=0.7, help="recall@5 门禁，低于则退出码 1")
    parser.add_argument("--trace", action="store_true", help="上传 runs 到 LangSmith（无 key 跳过）")
    args = parser.parse_args(argv)
    settings = get_settings()
    setup_logging(settings.log_level)

    if args.bench_rows:
        return bench_main(args.bench_rows)

    seed(args.synthetic_count)
    rows, probe = load_recipe_rows()
    texts = {r["recipe_id"]: build_text(r) for r in rows}
    cases = build_cases(50)
    evaluate = maybe_trace(evaluate, "eval_retrieval", args.trace)

    bm25_only = HybridRetriever(settings, enable_vector=False)
    recall_b, cov_b, used_b = evaluate(bm25_only, cases, texts)
    print(f"[BM25-only] 用例 {used_b}  recall@5={recall_b:.3f}  coverage={cov_b:.3f}")

    hybrid: HybridRetriever | None = None
    label = ""
    if args.fake_vector:
        store, embeddings = build_fake_vector_store(rows)
        corpus = BM25Corpus(settings, loader=lambda: (rows, probe))
        hybrid = HybridRetriever(
            settings, embeddings=embeddings, chroma=store, corpus=corpus
        )
        label = "混合(伪向量)"
    elif settings.embedding_api_key:
        hybrid = HybridRetriever(settings)
        label = "混合(真实向量)"
    if hybrid is not None:
        recall_h, cov_h, used_h = evaluate(hybrid, cases, texts)
        print(f"[{label}] 用例 {used_h}  recall@5={recall_h:.3f}  coverage={cov_h:.3f}")
        if recall_h < recall_b:
            print(f"警告：混合({recall_h:.3f}) < 单路({recall_b:.3f})，未达混合 ≥ 单路基线")
    else:
        print("[混合] 未启用（无 embedding key 且未指定 --fake-vector），跳过对比")

    if recall_b < args.min_recall:
        print(f"FAIL：recall@5={recall_b:.3f} < {args.min_recall}", file=sys.stderr)
        return 1
    print(f"PASS：recall@5={recall_b:.3f} ≥ {args.min_recall}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
