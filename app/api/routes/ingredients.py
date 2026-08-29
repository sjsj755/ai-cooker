"""GET /api/ingredients/search：食材联想（LIKE 优先，向量补充，失败回退）。"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_embedding_provider, get_ingredients_chroma
from app.core.embeddings import EmbeddingProvider
from app.core.logging import get_logger, log_event
from app.models import Ingredient
from app.schemas.ingredients import IngredientOut
from app.vector_store import ChromaStore

router = APIRouter()
logger = get_logger("app.api.ingredients")


@router.get("/search", response_model=list[IngredientOut])
async def search_ingredients(
    q: str = Query(min_length=1, description="食材关键词"),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    embeddings: EmbeddingProvider | None = Depends(get_embedding_provider),
    chroma: ChromaStore = Depends(get_ingredients_chroma),
) -> list[IngredientOut]:
    keyword = q.strip()
    if not keyword:
        raise HTTPException(status_code=400, detail="查询词不能为空")
    pattern = f"%{keyword}%"
    rows = db.scalars(
        select(Ingredient)
        .where(or_(Ingredient.name.like(pattern), Ingredient.aliases.like(pattern)))
        .order_by(Ingredient.name)
        .limit(limit)
    ).all()
    results = [IngredientOut.model_validate(r) for r in rows]
    if len(results) >= limit or embeddings is None:
        return results
    try:
        if chroma.count() == 0:
            return results
        vectors = await embeddings.embed_texts([keyword])
        hits = await chroma.query(vectors, n_results=limit * 4)
    except Exception as exc:  # noqa: BLE001 - 向量补充失败自动回退 LIKE-only
        log_event(
            logger,
            logging.WARNING,
            "ingredients.search.vector_fallback",
            error=f"{type(exc).__name__}: {exc}",
        )
        return results
    seen = {r.id for r in rows}
    extra_ids: list[int] = []
    for hit in hits:
        meta = hit.get("metadata") or {}
        ingredient_id = meta.get("ingredient_id")
        if ingredient_id is None or ingredient_id in seen:
            continue
        seen.add(ingredient_id)
        extra_ids.append(ingredient_id)
        if len(extra_ids) >= limit - len(results):
            break
    if extra_ids:
        extra_rows = db.scalars(
            select(Ingredient).where(Ingredient.id.in_(extra_ids))
        ).all()
        by_id = {r.id: r for r in extra_rows}
        results.extend(
            IngredientOut.model_validate(by_id[i]) for i in extra_ids if i in by_id
        )
    return results
