"""菜谱详情与检索响应。"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RecipeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    source_url: str
    difficulty: int | None = None
    cook_time_minutes: int | None = None
    servings: int | None = None
    steps: list[dict[str, Any]] | None = None
    description: str | None = None


class RecipeCandidateOut(BaseModel):
    """检索候选（API 出参；不含内部评分字段）。"""

    recipe_id: int
    title: str
    match_score: float
    missing_ingredients: list[str] = Field(default_factory=list)


class SearchResponse(BaseModel):
    """GET /api/recipes/search 响应。"""

    recipes: list[RecipeCandidateOut] = Field(default_factory=list)
    degraded: bool = False
    notice: str | None = None
