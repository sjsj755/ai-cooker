"""四级映射服务：精确 → 别名 → 包含 → 向量（ingredients_docs）。

把 LLM 抽取结果映射到 MySQL 食材字典；未命中 unknown=True。
向量级依赖嵌入可用；无 key / 集合为空 / 调用失败时自动降级为三级映射（不报错）。
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Callable

from sqlalchemy import select

from app.config import Settings, get_settings
from app.core.embeddings import EmbeddingProvider
from app.core.html_clean import clean_text
from app.core.logging import get_logger, log_event
from app.core.openai_embeddings import EmbeddingConfigError, OpenAICompatibleEmbeddings
from app.db.session import SessionLocal
from app.graph.state import ParsedIngredient
from app.models import Ingredient
from app.schemas.recommend import IngredientExtraction
from app.vector_store import ChromaStore

logger = get_logger("app.graph.linking")

_DictionaryRow = tuple[int, str, list[str]]


class IngredientLinker:
    """按四级顺序把单个抽取项映射到字典食材。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        session_factory: Callable = SessionLocal,
        embeddings: EmbeddingProvider | None = None,
        chroma: ChromaStore | None = None,
        similarity_threshold: float | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._session_factory = session_factory
        self._embeddings = embeddings
        self._chroma = chroma
        self._threshold = (
            similarity_threshold
            if similarity_threshold is not None
            else self._settings.link_vector_similarity_threshold
        )

    def _load_dictionary(self) -> list[_DictionaryRow]:
        with self._session_factory() as session:
            rows = session.execute(
                select(Ingredient.id, Ingredient.name, Ingredient.aliases)
            ).all()
        return [(r.id, r.name, list(r.aliases or [])) for r in rows]

    async def link(
        self, items: list[IngredientExtraction] | list[ParsedIngredient]
    ) -> list[ParsedIngredient]:
        dictionary = self._load_dictionary()
        result: list[ParsedIngredient] = []
        for item in items:
            raw = clean_text(item.name if isinstance(item, IngredientExtraction) else item.raw_name)
            if not raw:
                result.append(
                    ParsedIngredient(
                        raw_name=item.name
                        if isinstance(item, IngredientExtraction)
                        else item.raw_name,
                        unknown=True,
                    )
                )
                continue
            quantity = (
                item.quantity if isinstance(item, IngredientExtraction) else item.quantity
            )
            unit = item.unit if isinstance(item, IngredientExtraction) else item.unit
            matched = self._match_exact(raw, dictionary)
            if matched is None:
                matched = self._match_alias(raw, dictionary)
            if matched is None:
                matched = self._match_contains(raw, dictionary)
            if matched is None:
                matched = await self._match_vector(raw)
            if matched is None:
                result.append(
                    ParsedIngredient(
                        raw_name=raw, quantity=quantity, unit=unit, unknown=True
                    )
                )
            else:
                ing_id, name = matched
                result.append(
                    ParsedIngredient(
                        raw_name=raw,
                        normalized_name=name,
                        ingredient_id=ing_id,
                        quantity=quantity,
                        unit=unit,
                        unknown=False,
                    )
                )
        return result

    @staticmethod
    def _match_exact(
        raw: str, dictionary: list[_DictionaryRow]
    ) -> tuple[int, str] | None:
        for ing_id, name, _aliases in dictionary:
            if clean_text(name) == raw:
                return ing_id, name
        return None

    @staticmethod
    def _match_alias(
        raw: str, dictionary: list[_DictionaryRow]
    ) -> tuple[int, str] | None:
        for ing_id, name, aliases in dictionary:
            if any(clean_text(a) == raw for a in aliases):
                return ing_id, name
        return None

    @staticmethod
    def _match_contains(
        raw: str, dictionary: list[_DictionaryRow]
    ) -> tuple[int, str] | None:
        """名称双向包含；多个命中取字典名最长（最具体）者。"""
        hits = [
            (ing_id, name)
            for ing_id, name, _aliases in dictionary
            if clean_text(name) and (clean_text(name) in raw or raw in clean_text(name))
        ]
        if not hits:
            return None
        return max(hits, key=lambda hit: len(hit[1]))

    async def _match_vector(self, raw: str) -> tuple[int, str] | None:
        embeddings = self._get_embeddings()
        if embeddings is None:
            return None
        chroma = self._get_chroma()
        if chroma.count() == 0:
            return None
        try:
            vectors = await embeddings.embed_texts([raw])
            hits = await chroma.query(vectors, n_results=1)
        except Exception as exc:  # noqa: BLE001 - 向量级任一步失败即降级三级映射
            log_event(
                logger,
                logging.WARNING,
                "linking.vector_skipped",
                error=f"{type(exc).__name__}: {exc}",
            )
            return None
        for hit in hits:
            meta = hit.get("metadata") or {}
            distance = hit.get("distance")
            similarity = 1.0 - distance if distance is not None else 0.0
            ingredient_id = meta.get("ingredient_id")
            name = meta.get("name")
            if (
                ingredient_id is not None
                and isinstance(name, str)
                and similarity >= self._threshold
            ):
                return int(ingredient_id), name
        return None

    def _get_embeddings(self) -> EmbeddingProvider | None:
        if self._embeddings is not None:
            return self._embeddings
        if not self._settings.embedding_api_key:
            return None
        try:
            return OpenAICompatibleEmbeddings(self._settings)
        except EmbeddingConfigError:
            return None

    def _get_chroma(self) -> ChromaStore:
        if self._chroma is not None:
            return self._chroma
        return ChromaStore(
            self._settings, collection=self._settings.chroma_ingredients_collection
        )


@lru_cache
def get_ingredient_linker() -> IngredientLinker:
    """默认四级映射服务；测试可 monkeypatch / 覆盖。"""
    return IngredientLinker()
