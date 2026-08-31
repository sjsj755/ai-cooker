"""推荐 API 请求 / 响应，以及 parse / generate 的结构化输出 Schema。"""

from pydantic import BaseModel, Field

from app.graph.state import Recommendation


class RecommendRequest(BaseModel):
    ingredients: list[str] = Field(
        min_length=1, description="家里已有的食材（口语描述）"
    )
    exclude_tags: list[str] = Field(
        default_factory=list, description="忌口 / 过敏标签"
    )


class IngredientExtraction(BaseModel):
    """parse 节点（LLM 识别）单条输出。"""

    name: str = Field(description="食材名称")
    quantity: str | None = Field(default=None, description="数量，可为空")
    unit: str | None = Field(default=None, description="单位，可为空")


class IngredientExtractionList(BaseModel):
    """parse 节点结构化输出：食材抽取结果列表。"""

    items: list[IngredientExtraction] = Field(default_factory=list)


class RecommendationSet(BaseModel):
    """generate 节点结构化输出：推荐集合。"""

    recommendations: list[Recommendation] = Field(default_factory=list)


class RecommendResponse(BaseModel):
    recipes: list[Recommendation] = Field(default_factory=list)
    degraded: bool = False
    notice: str | None = None
    # P6.4：快路径临时降级标记。True 表示“AI 文案生成中，稍后自动更新”，
    # 前端据此渲染中性横幅并轮询 /api/recipes/recommend/status；其余 degraded
    # 保持既有“降级提示”语义
    ai_pending: bool = False


class RecommendStatusResponse(BaseModel):
    """P6.4 状态轮询：快响应后前端定期查询，AI 文案就绪即携带完整结果。"""

    ready: bool = False
    warming: bool = False
    result: RecommendResponse | None = None
