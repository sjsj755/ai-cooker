"""6 个空节点骨架：仅透传状态并标注 TODO，P1/P3 填充实现。"""

from app.graph.state import CookState


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
    """TODO(P2): 走 Retriever 接口混合召回 Top-50。"""
    return state


async def rank_node(state: CookState) -> CookState:
    """TODO(P2): 走 ScoringStrategy 评分并取 Top-5。"""
    return state


async def generate_node(state: CookState) -> CookState:
    """TODO(P3): 走 LLMProvider.structured 生成结构化推荐文案。"""
    return state
