"""LangGraph 状态 Schema（Pydantic BaseModel，字段默认值即状态通道默认值）。

LangGraph 1.2.11 中，TypedDict(total=False) 状态 Schema 不会自动填充通道默认值，
改为 BaseModel 后直接 ainvoke 未初始化状态也能得到 retry_count=0 等默认键。
节点返回约定：一律 {**state.model_dump(), ...更新项}（全量展开），保证
last-value-wins 合并语义下未更新键（含 retry_count）由旧状态保留。
"""

from pydantic import BaseModel, Field

from app.core.retriever import RecipeCandidate
from app.schemas.recipes import IngredientItem


class ParsedIngredient(BaseModel):
    """parse 节点输出；unknown=True 表示字典映射未命中。"""

    raw_name: str
    normalized_name: str | None = None
    ingredient_id: int | None = None
    quantity: str | None = None
    unit: str | None = None
    unknown: bool = False


class Recommendation(BaseModel):
    """generate 节点最终推荐；seasonings 由 MySQL 回填（以事实为准，不信 LLM）。"""

    recipe_id: int
    title: str
    match_score: float
    missing_ingredients: list[str] = Field(default_factory=list)
    difficulty: int | None = None
    cook_time_minutes: int | None = None
    steps: list[dict] | None = None
    tips: str | None = None
    seasonings: list[IngredientItem] = Field(default_factory=list)


class CookState(BaseModel):
    """工作流状态；retry_count 由状态机层强制约束防死循环。"""

    query: str = ""
    ingredients: list[str] = Field(default_factory=list)
    exclude_tags: list[str] = Field(default_factory=list)
    parsed_ingredients: list[ParsedIngredient] = Field(default_factory=list)
    candidates: list[RecipeCandidate] = Field(default_factory=list)
    ranked: list[RecipeCandidate] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    retry_count: int = 0
    parse_error: bool = False
    degraded: bool = False
    notice: str | None = None
    # P6.4：快路径标记。True 时 generate_node 直接走 MySQL 原文降级补全
    # （不调用 LLM），由路由层随后台任务补全 AI 文案并刷新缓存
    fast_first: bool = False
    # P6.4：快响应临时降级标记，路由层映射到 RecommendResponse.ai_pending
    ai_pending: bool = False


def empty_state(
    ingredients: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    query: str = "",
    fast_first: bool = False,
) -> CookState:
    """构造带默认值的初始状态，便于空状态跑通验收。"""
    return CookState(
        query=query,
        ingredients=ingredients or [],
        exclude_tags=exclude_tags or [],
        fast_first=fast_first,
    )
