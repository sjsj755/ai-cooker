"""GET /api/recipes/{id} 与 GET /api/recipes/search（检索验证端点）。"""

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_ranking_service
from app.core.logging import get_logger, log_event
from app.models import Ingredient, Recipe, RecipeIngredient
from app.retrieval.errors import RetrievalUnavailableError
from app.retrieval.ranking import RankingService
from app.schemas.recipes import (
    IngredientItem,
    RecipeCandidateOut,
    RecipeOut,
    SearchResponse,
)

router = APIRouter()
logger = get_logger("app.api.recipes")

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _parse_csv(raw: str, max_items: int, max_len: int) -> list[str]:
    if not raw:
        return []
    items = [p.strip() for p in raw.split(",")]
    items = [p for p in items if p]
    if len(items) > max_items:
        raise HTTPException(status_code=400, detail=f"参数项数不能超过 {max_items}")
    for item in items:
        if len(item) > max_len:
            raise HTTPException(status_code=400, detail=f"单项长度不能超过 {max_len}")
        if _CONTROL_CHARS.search(item):
            raise HTTPException(status_code=400, detail="参数包含非法控制字符")
    return items


@router.get("/search", response_model=SearchResponse)
async def search_recipes(
    q: str = Query(..., description="菜谱检索文本（必填）"),
    ingredients: str = Query(default="", description="已有食材，逗号分隔"),
    exclude_tags: str = Query(default="", description="忌口标签，逗号分隔"),
    limit: int = Query(default=10),
    ranking: RankingService = Depends(get_ranking_service),
) -> SearchResponse:
    query = q.strip()
    if not query:
        raise HTTPException(status_code=400, detail="查询词不能为空")
    if len(query) > 200:
        raise HTTPException(status_code=400, detail="查询词不能超过 200 字符")
    if _CONTROL_CHARS.search(query):
        raise HTTPException(status_code=400, detail="查询词包含非法控制字符")
    if not (1 <= limit <= 50):
        raise HTTPException(status_code=400, detail="limit 必须在 1-50 之间")
    available = _parse_csv(ingredients, max_items=30, max_len=50)
    excluded = _parse_csv(exclude_tags, max_items=20, max_len=50)
    try:
        result = await ranking.rank(
            query,
            available_ingredients=available,
            exclude_tags=excluded,
            top_k=limit,
        )
    except RetrievalUnavailableError as exc:
        log_event(
            logger,
            logging.ERROR,
            "retrieval.search.failed",
            error=str(exc),
            http_status=503,
        )
        raise HTTPException(status_code=503, detail="检索服务暂不可用，请稍后重试") from exc
    except Exception as exc:  # noqa: BLE001 - 未预期异常落 ERROR 日志后返回 500
        log_event(
            logger,
            logging.ERROR,
            "retrieval.search.failed",
            error=f"{type(exc).__name__}: {exc}",
            http_status=500,
        )
        raise HTTPException(status_code=500, detail="检索服务异常") from exc
    return SearchResponse(
        recipes=[
            RecipeCandidateOut(
                recipe_id=c.recipe_id,
                title=c.title,
                match_score=c.match_score,
                missing_ingredients=c.missing_ingredients,
            )
            for c in result.recipes
        ],
        degraded=result.degraded,
        notice=result.notice,
    )


@router.get("/{recipe_id}", response_model=RecipeOut)
def get_recipe(recipe_id: int, db: Session = Depends(get_db)) -> RecipeOut:
    recipe = db.scalar(select(Recipe).where(Recipe.id == recipe_id))
    if recipe is None:
        raise HTTPException(status_code=404, detail="菜谱不存在")
    ingredients, seasonings = _load_ingredients(db, recipe_id)
    return RecipeOut(
        id=recipe.id,
        title=recipe.title,
        source_url=recipe.source_url,
        difficulty=recipe.difficulty,
        cook_time_minutes=recipe.cook_time_minutes,
        servings=recipe.servings,
        steps=recipe.steps,
        description=recipe.description,
        ingredients=ingredients,
        seasonings=seasonings,
    )


def _load_ingredients(
    db: Session, recipe_id: int
) -> tuple[list[IngredientItem], list[IngredientItem]]:
    """查询菜谱用料，按 category=='调料' 拆分为 食材 / 调料（保持 join 顺序）。"""
    rows = db.execute(
        select(
            Ingredient.name,
            RecipeIngredient.amount,
            Ingredient.category,
        )
        .join(Ingredient, Ingredient.id == RecipeIngredient.ingredient_id)
        .where(RecipeIngredient.recipe_id == recipe_id)
        .order_by(RecipeIngredient.ingredient_id)
    ).all()
    ingredients: list[IngredientItem] = []
    seasonings: list[IngredientItem] = []
    for name, amount, category in rows:
        item = IngredientItem(name=name, amount=amount)
        if category == "调料":
            seasonings.append(item)
        else:
            ingredients.append(item)
    return ingredients, seasonings
