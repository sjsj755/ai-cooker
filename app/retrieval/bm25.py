"""BM25Corpus：中文 bigram 分词、语料缓存（探针感知内容更新）与搜索。"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
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
        count, max_id, max_updated = session.execute(
            select(
                func.count(Recipe.id),
                func.coalesce(func.max(Recipe.id), 0),
                func.max(Recipe.updated_at),
            )
        ).one()
        probe = (
            int(count),
            int(max_id),
            max_updated.isoformat() if max_updated else None,
        )
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


class BM25Corpus:
    """语料缓存 + 搜索；探针变化或上次构建失败时自动重建（双缓冲 + 锁）。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        session_factory: Callable = SessionLocal,
        loader: Loader | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._session_factory = session_factory
        self._loader = loader or (lambda: load_recipe_rows(session_factory))
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
