"""HybridRetriever：BM25 + Chroma 向量双路召回，RRF 融合与降级。"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from sqlalchemy import select

from app.config import Settings, get_settings
from app.core.html_clean import clean_text
from app.core.logging import get_logger, log_event
from app.core.openai_embeddings import EmbeddingConfigError, OpenAICompatibleEmbeddings
from app.core.retriever import RecipeCandidate, Retriever
from app.db.session import SessionLocal
from app.models import Recipe
from app.retrieval.bm25 import BM25Corpus
from app.retrieval.errors import RetrievalUnavailableError
from app.retrieval.fusion import rrf
from app.vector_store import ChromaStore


class HybridRetriever(Retriever):
    """双路召回：BM25（bigram）与向量（Chroma 按块聚合），RRF 融合。

    向量路四态：
      ① 跳过/失败（无 key、集合空、异常）→ BM25-only + degraded=True；
      ② 成功但 raw hits=0 → BM25-only + degraded=False（正常无匹配）；
      ③ 部分孤儿 → 保留有效命中 + WARN；
      ④ 全部孤儿 → 向量路整路丢弃 + degraded=True。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        embeddings=None,
        chroma: ChromaStore | None = None,
        corpus: BM25Corpus | None = None,
        session_factory: Callable = SessionLocal,
        enable_vector: bool = True,
    ) -> None:
        self._settings = settings or get_settings()
        self._embeddings = embeddings
        self._chroma = chroma
        self._corpus = corpus
        self._session_factory = session_factory
        self._enable_vector = enable_vector
        self._logger = get_logger("app.retrieval")
        self.last_notice: str | None = None
        self._last_degraded_reason: str | None = None

    def _get_chroma(self) -> ChromaStore:
        if self._chroma is not None:
            return self._chroma
        # P5 压测：实例内缓存 ChromaStore，避免每请求重建持久化客户端
        self._chroma = ChromaStore(self._settings)
        return self._chroma

    def _get_corpus(self) -> BM25Corpus:
        if self._corpus is not None:
            return self._corpus
        # P5 压测：实例内缓存 BM25Corpus，避免每请求全量重建索引
        self._corpus = BM25Corpus(
            settings=self._settings, session_factory=self._session_factory
        )
        return self._corpus

    def _get_embeddings(self):
        if self._embeddings is not None:
            return self._embeddings
        if not self._settings.embedding_api_key:
            return None
        try:
            return OpenAICompatibleEmbeddings(self._settings)
        except EmbeddingConfigError:
            return None

    async def retrieve(self, query: str, top_k: int = 50) -> list[RecipeCandidate]:
        started = time.perf_counter()
        log_event(
            self._logger,
            logging.INFO,
            "retrieval.query.started",
            query_len=len(query or ""),
            top_k=top_k,
        )
        try:
            candidates, degraded, reason = await self._retrieve(query, top_k)
        except Exception:
            log_event(
                self._logger,
                logging.ERROR,
                "retrieval.query.failed",
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
            )
            raise
        log_event(
            self._logger,
            logging.INFO,
            "retrieval.query.done",
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            candidates=len(candidates),
            degraded=degraded,
            degraded_reason=reason,
        )
        # 降级原因（如无 embedding key）是静态配置态：仅首次/变化时告警一次，
        # 避免 BM25-only 压测下每请求刷 WARNING（日志 I/O 也计入 P95）。
        if (
            degraded
            and reason
            and reason != self._last_degraded_reason
        ):
            log_event(
                self._logger,
                logging.WARNING,
                "retrieval.query.degraded",
                degraded_reason=reason,
            )
            self._last_degraded_reason = reason
        elif not degraded:
            self._last_degraded_reason = None
        return candidates

    async def _retrieve(
        self, query: str, top_k: int
    ) -> tuple[list[RecipeCandidate], bool, str | None]:
        query = clean_text(query)
        if not query:
            self.last_notice = None
            return [], False, None

        corpus = self._get_corpus()
        await corpus.ensure_built()

        reasons: list[str] = []
        if corpus.degraded_notice:
            reasons.append(corpus.degraded_notice)

        k = self._settings.retrieval_fusion_rrf_k
        w_bm25 = self._settings.retrieval_bm25_weight
        w_vector = self._settings.retrieval_vector_weight

        bm25_hits = await corpus.search(query, top_k * 2)
        bm25_terms: dict[int, float] = {}
        for rank, (recipe_id, _score) in enumerate(bm25_hits, start=1):
            bm25_terms[recipe_id] = rrf([rank], k, w_bm25)

        vector_terms: dict[int, float] = {}
        vector_meta: dict[int, dict] = {}
        vector_degraded_reason: str | None = None
        embeddings = self._get_embeddings()
        if not self._enable_vector:
            vector_degraded_reason = "向量路未启用，仅关键词检索"
        elif embeddings is None:
            vector_degraded_reason = "EMBEDDING_API_KEY 未配置，已回退关键词检索"
        else:
            # 只有真正要跑向量路时才触碰 Chroma（避免 BM25-only 每请求 count 开销）
            chroma = self._get_chroma()
            if chroma.count() == 0:
                vector_degraded_reason = "Chroma 集合为空，已回退关键词检索"
                chroma = None
        if embeddings is not None and vector_degraded_reason is None:
            chroma = self._get_chroma()
            try:
                query_vectors = await embeddings.embed_texts([query])
                hits = await chroma.query(
                    query_vectors,
                    n_results=top_k * self._settings.retrieval_vector_query_multiplier,
                )
                # 相似度阈值：过滤无关噪声块（Chroma 无内置阈值，恒返回 top_n）
                max_distance = self._settings.retrieval_vector_max_distance
                hits = [
                    h
                    for h in hits
                    if (h.get("distance") if h.get("distance") is not None else 1.0)
                    <= max_distance
                ]
            except RetrievalUnavailableError:
                raise
            except Exception as exc:  # noqa: BLE001 - 向量路任一步失败即降级
                vector_degraded_reason = (
                    f"向量检索不可用：{type(exc).__name__}，已回退关键词检索"
                )
                log_event(
                    self._logger,
                    logging.WARNING,
                    "retrieval.query.degraded",
                    degraded_reason=vector_degraded_reason,
                    error=str(exc),
                )
            else:
                if hits:
                    grouped: dict[str, list[int]] = {}
                    for idx, hit in enumerate(hits, start=1):
                        meta = hit.get("metadata") or {}
                        url = meta.get("source_url")
                        if url:
                            grouped.setdefault(url, []).append(idx)
                    try:
                        lookup = await asyncio.to_thread(
                            self._lookup_recipes, list(grouped)
                        )
                    except Exception as exc:  # noqa: BLE001 - MySQL 反查异常 → 503
                        raise RetrievalUnavailableError(
                            f"向量命中反查菜谱失败: {type(exc).__name__}: {exc}"
                        ) from exc
                    valid = {
                        url: ranks for url, ranks in grouped.items() if url in lookup
                    }
                    orphan_count = len(grouped) - len(valid)
                    if orphan_count:
                        log_event(
                            self._logger,
                            logging.WARNING,
                            "retrieval.vector.orphan_chunks",
                            orphan_count=orphan_count,
                            total_urls=len(grouped),
                        )
                    if grouped and not valid:
                        vector_degraded_reason = (
                            "向量检索结果与菜谱库不一致（全部为孤儿块），已回退关键词检索"
                        )
                    else:
                        for url, ranks in valid.items():
                            info = lookup[url]
                            recipe_id = info["recipe_id"]
                            vector_terms[recipe_id] = rrf(ranks, k, w_vector)
                            vector_meta[recipe_id] = {
                                "title": info["title"],
                                "difficulty": info["difficulty"],
                                "cook_time_minutes": info["cook_time_minutes"],
                            }

        if vector_degraded_reason:
            reasons.append(vector_degraded_reason)
        degraded = bool(reasons)
        self.last_notice = "；".join(reasons) if reasons else None

        all_ids = set(bm25_terms) | set(vector_terms)
        candidates: list[RecipeCandidate] = []
        for recipe_id in all_ids:
            meta = {**corpus.meta(recipe_id), **vector_meta.get(recipe_id, {})}
            candidates.append(
                RecipeCandidate(
                    recipe_id=recipe_id,
                    title=meta.get("title") or "",
                    match_score=bm25_terms.get(recipe_id, 0.0)
                    + vector_terms.get(recipe_id, 0.0),
                    degraded=degraded,
                    difficulty=meta.get("difficulty"),
                    cook_time_minutes=meta.get("cook_time_minutes"),
                )
            )
        candidates.sort(key=lambda c: (-c.match_score, c.recipe_id))
        return candidates[:top_k], degraded, self.last_notice

    def _lookup_recipes(self, urls: list[str]) -> dict[str, dict]:
        if not urls:
            return {}
        with self._session_factory() as session:
            rows = session.execute(
                select(
                    Recipe.source_url,
                    Recipe.id,
                    Recipe.title,
                    Recipe.difficulty,
                    Recipe.cook_time_minutes,
                ).where(Recipe.source_url.in_(urls))
            ).all()
        return {
            r.source_url: {
                "recipe_id": r.id,
                "title": r.title,
                "difficulty": r.difficulty,
                "cook_time_minutes": r.cook_time_minutes,
            }
            for r in rows
        }
