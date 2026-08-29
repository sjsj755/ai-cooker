"""GET /api/recipes/{id}：菜谱详情。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models import Recipe
from app.schemas.recipes import RecipeOut

router = APIRouter()


@router.get("/{recipe_id}", response_model=RecipeOut)
def get_recipe(recipe_id: int, db: Session = Depends(get_db)) -> RecipeOut:
    recipe = db.scalar(select(Recipe).where(Recipe.id == recipe_id))
    if recipe is None:
        raise HTTPException(status_code=404, detail="菜谱不存在")
    return RecipeOut.model_validate(recipe)
