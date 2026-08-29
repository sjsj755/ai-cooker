"""LangGraph 节点：P2 填充 retrieve/rank，其余保持 P3 占位。"""

from app.graph.state import CookState
from app.retrieval.errors import RetrievalUnavailableError
from app.retrieval.ranking import get_ranking_service


async def parse_node(state: CookState) -> CookState:
    """TODO(P3): 走 LLMProvider.structured 识别自由文本中的食材。"""
    return state


async def link_node(state: CookState) -> CookState:
    """TODO(P3): 四级映射（精确→别名→包含→向量）到食材词典。"""
    return state


async def filter_node(state: CookState) -> CookState:
    """TODO(P3): 按 exclude_tags 过滤忌口 / 过敏标签。"""
    return state


async def retrieve_node(state: CookState) -> CookState:
    """P2：以 state.query 为唯一检索文本，ingredients 仅进缺料计算。"""
    query = (state.get("query") or "").strip()
    if not query:
        return {
            **state,
            "candidates": [],
            "degraded": bool(state.get("degraded")),
            "notice": "缺少查询文本",
        }
    service = get_ranking_service()
    try:
        result = await service.rank(
            query,
            available_ingredients=state.get("ingredients") or [],
            exclude_tags=state.get("exclude_tags") or [],
        )
    except RetrievalUnavailableError:
        return {
            **state,
            "candidates": [],
            "degraded": True,
            "notice": "检索服务暂不可用，请稍后重试",
        }
    return {
        **state,
        "candidates": result.recipes,
        "degraded": bool(state.get("degraded")) or result.degraded,
        "notice": result.notice,
    }


async def rank_node(state: CookState) -> CookState:
    """P2：候选已按缺料数/评分/recipe_id 字典序排好，取 Top-5 写入 ranked。"""
    return {**state, "ranked": (state.get("candidates") or [])[:5]}


async def generate_node(state: CookState) -> CookState:
    """TODO(P3): 走 LLMProvider.structured 生成结构化推荐文案。"""
    return state
