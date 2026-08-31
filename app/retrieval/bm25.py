"""BM25Corpus：中文 bigram 分词、语料缓存（探针感知内容更新）与搜索。"""

from __future__ import annotations

import asyncio
import logging
import os
import pickle
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rank_bm25 import BM25Okapi
from sqlalchemy import func, select

from app.config import Settings, get_settings
from app.core.html_clean import clean_text
from app.core.logging import get_logger, log_event
from app.db.session import SessionLocal
from app.models import Ingredient, Recipe, RecipeIngredient, RecipeTag, Tag
from app.retrieval.errors import RetrievalUnavailableError

_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_ASCII_RUN = re.compile(r"[A-Za-z0-9]+")

Loader = Callable[[], tuple[list[dict[str, Any]], tuple]]

# P6.4：磁盘缓存载荷版本（索引结构/分词变化时递增，旧缓存自动作废）
BM25_CACHE_VERSION = 1


def tokenize(text: str) -> list[str]:
    """中文按相邻两字切 bigram；非中文按字母数字串切；单字中文保留单字。"""
    tokens: list[str] = []
    for run in _CJK_RUN.findall(text or ""):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    for run in _ASCII_RUN.findall(text or ""):
        tokens.append(run.lower())
    return tokens


def build_text(row: dict[str, Any]) -> str:
    """语料 = 标题 + 描述 + 非调料食材名（含别名）+ 调料名（含别名）+ 标签名。"""
    parts: list[str] = [row.get("title") or "", row.get("description") or ""]
    for item in row.get("ingredients", []) + row.get("seasonings", []):
        name = item.get("name") or ""
        if name:
            parts.append(name)
        for alias in item.get("aliases", []) or []:
            if isinstance(alias, str) and alias:
                parts.append(alias)
    parts.extend(t for t in (row.get("tags") or []) if t)
    return clean_text(" ".join(p for p in parts if p))


def load_recipe_rows(session_factory: Callable = SessionLocal):
    """从 MySQL 加载语料行与缓存探针 (COUNT(*), MAX(id), MAX(updated_at))。"""
    with session_factory() as session:
        probe = corpus_probe(session_factory=session_factory, session=session)
        recipe_rows = session.execute(
            select(
                Recipe.id,
                Recipe.title,
                Recipe.description,
                Recipe.difficulty,
                Recipe.cook_time_minutes,
                Recipe.source_url,
            )
        ).all()
        ing_rows = session.execute(
            select(
                RecipeIngredient.recipe_id,
                Ingredient.name,
                Ingredient.category,
                Ingredient.aliases,
            ).join(Ingredient, Ingredient.id == RecipeIngredient.ingredient_id)
        ).all()
        tag_rows = session.execute(
            select(RecipeTag.recipe_id, Tag.name).join(
                Tag, Tag.id == RecipeTag.tag_id
            )
        ).all()

    by_id: dict[int, dict[str, Any]] = {}
    for r in recipe_rows:
        by_id[r.id] = {
            "recipe_id": r.id,
            "title": r.title,
            "description": r.description,
            "difficulty": r.difficulty,
            "cook_time_minutes": r.cook_time_minutes,
            "source_url": r.source_url,
            "ingredients": [],
            "seasonings": [],
            "tags": [],
        }
    for rid, name, category, aliases in ing_rows:
        if rid not in by_id:
            continue
        item = {"name": name, "aliases": aliases or []}
        if category == "调料":
            by_id[rid]["seasonings"].append(item)
        else:
            by_id[rid]["ingredients"].append(item)
    for rid, tag_name in tag_rows:
        if rid in by_id:
            by_id[rid]["tags"].append(tag_name)
    return list(by_id.values()), probe


def corpus_probe(
    session_factory: Callable = SessionLocal, session=None
) -> tuple:
    """轻量缓存探针 (COUNT(*), MAX(id), MAX(updated_at))：内容未变则跳过全量重载。"""
    if session is None:
        with session_factory() as session:
            return _corpus_probe_in_session(session)
    return _corpus_probe_in_session(session)


def _corpus_probe_in_session(session) -> tuple:
    count, max_id, max_updated = session.execute(
        select(
            func.count(Recipe.id),
            func.coalesce(func.max(Recipe.id), 0),
            func.max(Recipe.updated_at),
        )
    ).one()
    return (
        int(count),
        int(max_id),
        max_updated.isoformat() if max_updated else None,
    )


class BM25Corpus:
    """语料缓存 + 搜索；探针变化或上次构建失败时自动重建（双缓冲 + 锁）。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        session_factory: Callable = SessionLocal,
        loader: Loader | None = None,
        probe_loader: Callable[[], tuple] | None = None,
        probe_ttl_seconds: float = 1.0,
        cache_file: str | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._session_factory = session_factory
        self._loader = loader or (lambda: load_recipe_rows(session_factory))
        # 自定义 loader（隔离语料）不启用探针快路径，保持既有全量重载语义；
        # 默认 loader 配轻量探针：语料未变时每请求只跑 COUNT/MAX 查询，不重载全量行。
        self._probe_loader = probe_loader
        if self._probe_loader is None and loader is None:
            self._probe_loader = lambda: corpus_probe(session_factory)
        # 探针 TTL：语料内容变化最多延迟 N 秒被感知（仅默认 loader 路径，
        # 自定义 loader 走全量重载语义；压测下避免每请求 COUNT/MAX 查询）
        self._probe_ttl_seconds = probe_ttl_seconds
        # P6.4：磁盘缓存仅用于默认 loader（自定义 loader 走全量重载语义）；
        # 显式传入 cache_file 时始终启用（供测试用 tmp 路径）
        self._cache_file = cache_file or (
            self._settings.bm25_cache_file
            if self._settings.bm25_cache_enabled and loader is None
            else ""
        )
        self._use_cache = bool(self._cache_file)
        self._last_probe_at = 0.0
        self._lock = asyncio.Lock()
        self._built = False
        self._probe: tuple | None = None
        self._index: BM25Okapi | None = None
        self._doc_ids: list[int] = []
        self._meta: dict[int, dict[str, Any]] = {}
        self._degraded_notice: str | None = None
        self._logger = get_logger("app.retrieval.bm25")

    @property
    def degraded_notice(self) -> str | None:
        return self._degraded_notice

    def meta(self, recipe_id: int) -> dict[str, Any]:
        return self._meta.get(recipe_id, {})

    async def ensure_built(self) -> None:
        """按探针校验并重建；失败态按四态表处理（见 P2 计划）。"""
        # P6.4 磁盘缓存快路径：未构建时先尝试加载落盘索引，
        # 轻量探针一致则直接复用（重启后约 1-2s，不再全量重建）
        if not self._built and self._use_cache:
            loaded = await asyncio.to_thread(self._load_cache)
            if loaded is not None:
                index, doc_ids, meta, probe = loaded
                try:
                    current_probe = await asyncio.to_thread(self._probe_loader)
                except Exception as exc:  # noqa: BLE001 - 探针失败不采用磁盘缓存
                    log_event(
                        self._logger,
                        logging.WARNING,
                        "retrieval.corpus.cache_probe_failed",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                else:
                    if current_probe == probe:
                        self._index = index
                        self._doc_ids = doc_ids
                        self._meta = meta
                        self._probe = probe
                        self._built = True
                        self._degraded_notice = None
                        self._last_probe_at = time.monotonic()
                        return
        # 快路径：语料已构建且未降级时先跑轻量探针，内容未变直接复用缓存索引，
        # 避免每请求全量重载 MySQL 语料行（P5 10k/50k 压测门禁的依赖前提）。
        if self._built and self._degraded_notice is None and self._probe_loader is not None:
            if time.monotonic() - self._last_probe_at < self._probe_ttl_seconds:
                return  # TTL 内跳过探针查询，直接复用缓存索引
            # 先盖时间戳再 await：并发请求共享同一探针窗口，避免全员同时打 COUNT/MAX
            self._last_probe_at = time.monotonic()
            try:
                probe = await asyncio.to_thread(self._probe_loader)
            except Exception as exc:  # noqa: BLE001 - 探针失败回退缓存并降级提示
                self._degraded_notice = "关键词索引更新失败，已回退缓存数据"
                log_event(
                    self._logger,
                    logging.ERROR,
                    "retrieval.corpus.probe_failed",
                    error=f"{type(exc).__name__}: {exc}",
                    degraded=True,
                )
                return
            if probe == self._probe:
                return
        try:
            rows, probe = await asyncio.to_thread(self._loader)
        except Exception as exc:  # noqa: BLE001 - 统一按 MySQL 故障处理
            if self._index is not None or self._built:
                self._degraded_notice = "关键词索引更新失败，已回退缓存数据"
                log_event(
                    self._logger,
                    logging.ERROR,
                    "retrieval.corpus.rebuild_failed",
                    error=f"{type(exc).__name__}: {exc}",
                    degraded=True,
                )
                return
            raise RetrievalUnavailableError(
                f"关键词语料加载失败: {type(exc).__name__}: {exc}"
            ) from exc

        if self._built and probe == self._probe and self._degraded_notice is None:
            return

        async with self._lock:
            if self._built and probe == self._probe and self._degraded_notice is None:
                return
            try:
                doc_ids = [r["recipe_id"] for r in rows]
                meta = {r["recipe_id"]: r for r in rows}
                corpus = [build_text(r) for r in rows]
                index = BM25Okapi([tokenize(t) for t in corpus]) if corpus else None
            except Exception as exc:  # noqa: BLE001 - 非 MySQL 构建失败
                log_event(
                    self._logger,
                    logging.ERROR,
                    "retrieval.corpus.build_failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
                if self._built:
                    self._degraded_notice = "关键词索引更新失败，已回退缓存数据"
                    return
                self._index = None
                self._doc_ids = []
                self._meta = {}
                self._probe = probe
                self._built = True
                self._degraded_notice = "关键词索引构建失败"
                return
            # 双缓冲：全部构建完成后原子替换引用
            self._index = index
            self._doc_ids = doc_ids
            self._meta = meta
            self._probe = probe
            self._built = True
            self._degraded_notice = None
            if self._use_cache:
                try:
                    await asyncio.to_thread(
                        self._save_cache, doc_ids, meta, probe, index
                    )
                except Exception as exc:  # noqa: BLE001 - 落盘失败仅告警
                    log_event(
                        self._logger,
                        logging.WARNING,
                        "retrieval.corpus.cache_write_failed",
                        error=f"{type(exc).__name__}: {exc}",
                    )

    def _load_cache(self):
        """读取落盘索引；缺失/损坏/版本不符一律返回 None（走全量重建）。"""
        path = Path(self._cache_file)
        if not path.exists():
            return None
        try:
            with open(path, "rb") as fh:
                payload = pickle.load(fh)
            if (
                not isinstance(payload, dict)
                or payload.get("version") != BM25_CACHE_VERSION
            ):
                return None
            return (
                payload["index"],
                payload["doc_ids"],
                payload["meta"],
                payload["probe"],
            )
        except Exception:  # noqa: BLE001 - 缓存损坏按缺失处理
            return None

    def _save_cache(
        self,
        doc_ids: list[int],
        meta: dict[int, dict[str, Any]],
        probe: tuple,
        index: BM25Okapi | None,
    ) -> None:
        """原子落盘（tmp + os.replace），避免半写文件被下次启动读到。"""
        path = Path(self._cache_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": BM25_CACHE_VERSION,
            "index": index,
            "doc_ids": doc_ids,
            "meta": meta,
            "probe": probe,
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "wb") as fh:
            pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, path)

    def search_sync(self, query: str, top_k: int) -> list[tuple[int, float]]:
        """BM25 搜索；注意 rank_bm25 在极少数文档（1-2 条）时命中 token 的 idf 可能为负，
        会返回空结果——本机验收语料（7 条真实 + 合成）不受影响，P5 可再评估 epsilon 平滑。"""
        if self._index is None or not self._doc_ids:
            return []
        tokens = tokenize(query)
        if not tokens:
            return []
        scores = self._index.get_scores(tokens)
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        hits = [(self._doc_ids[i], float(scores[i])) for i in ranked if scores[i] > 0]
        return hits[:top_k]

    async def search(
        self, query: str, top_k: int
    ) -> list[tuple[int, float]]:
        return await asyncio.to_thread(self.search_sync, query, top_k)
