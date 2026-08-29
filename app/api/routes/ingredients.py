"""GET /api/ingredients/search：食材联想（MySQL 词典）。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models import Ingredient
from app.schemas.ingredients import IngredientOut

router = APIRouter()


@router.get("/search", response_model=list[IngredientOut])
def search_ingredients(
    q: str = Query(min_length=1, description="食材关键词"),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[IngredientOut]:
    keyword = q.strip()
    if not keyword:
        raise HTTPException(status_code=400, detail="查询词不能为空")
    # 全量参数化 + LIKE 走 name 唯一索引（前缀匹配）
    pattern = f"%{keyword}%"
    rows = db.scalars(
        select(Ingredient)
        .where(or_(Ingredient.name.like(pattern), Ingredient.aliases.like(pattern)))
        .order_by(Ingredient.name)
        .limit(limit)
    ).all()
    return [IngredientOut.model_validate(r) for r in rows]
