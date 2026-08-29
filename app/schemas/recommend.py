"""推荐 API 请求 / 响应（P0 仅占位）。"""

from pydantic import BaseModel, Field


class RecommendRequest(BaseModel):
    ingredients: list[str] = Field(min_length=1, description="家里已有的食材（口语描述）")
    exclude_tags: list[str] = Field(default_factory=list, description="忌口 / 过敏标签")


class RecommendResponse(BaseModel):
    recipes: list[dict] = Field(default_factory=list)
    degraded: bool = False
    notice: str | None = None
