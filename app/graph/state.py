"""LangGraph 状态 Schema（向后兼容扩展：新环节 = 新节点 + 新字段）。"""

from typing import TypedDict

from pydantic import BaseModel, Field

from app.core.retriever import RecipeCandidate


class ParsedIngredient(BaseModel):
    """parse 节点输出；unknown=True 表示词典映射未命中。"""

    raw_name: str
    normalized_name: str | None = None
    ingredient_id: int | None = None
    quantity: str | None = None
    unit: str | None = None
    unknown: bool = False


class Recommendation(BaseModel):
    """generate 节点最终推荐。"""

    recipe_id: int
    title: str
    match_score: float
    missing_ingredients: list[str] = Field(default_factory=list)
    difficulty: int | None = None
    cook_time_minutes: int | None = None
    steps: list[dict] | None = None
    tips: str | None = None


class CookState(TypedDict, total=False):
    """工作流状态；retry_count 由状态机层强制约束防死循环。"""

    query: str
    ingredients: list[str]
    exclude_tags: list[str]
    parsed_ingredients: list[ParsedIngredient]
    candidates: list[RecipeCandidate]
    ranked: list[RecipeCandidate]
    recommendations: list[Recommendation]
    retry_count: int
    degraded: bool
    notice: str | None


def empty_state(
    ingredients: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    query: str = "",
) -> CookState:
    """构造带默认值的初始状态，便于空状态跑通验收。"""
    return CookState(
        query=query,
        ingredients=ingredients or [],
        exclude_tags=exclude_tags or [],
        parsed_ingredients=[],
        candidates=[],
        ranked=[],
        recommendations=[],
        retry_count=0,
        degraded=False,
        notice=None,
    )
