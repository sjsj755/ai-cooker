"""SQLAlchemy 模型（6 张表）。导入即注册到 Base.metadata，供 Alembic 使用。"""

from app.models.ingredient import Ingredient, RecipeIngredient
from app.models.recipe import Recipe
from app.models.tag import Tag, RecipeTag
from app.models.feedback import UserFeedback

__all__ = [
    "Ingredient",
    "Recipe",
    "RecipeIngredient",
    "Tag",
    "RecipeTag",
    "UserFeedback",
]
