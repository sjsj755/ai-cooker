"""菜谱详情响应。"""

from typing import Any

from pydantic import BaseModel, ConfigDict


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
