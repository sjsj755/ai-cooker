"""build_graph()：parse → link → filter → retrieve → rank → generate 完整工作流。

条件边：
- parse 重试是唯一决策点：parse_error 且 retry_count ≤ MAX → 回 parse，
  超限 → degrade_end（degraded + notice）；
- filter 后 query 为空 → degrade_end（不进入检索）；
- retrieve 后候选为空 → 结束（notice 已由 retrieve/rank 给出）。
"""

from langgraph.graph import END, START, StateGraph

from app.config import get_settings
from app.graph.nodes import (
    degrade_end_node,
    filter_node,
    generate_node,
    link_node,
    parse_node,
    rank_node,
    retrieve_node,
)
from app.graph.state import CookState


def _route_after_parse(state: CookState) -> str:
    if not state.parse_error:
        return "link"
    if state.retry_count <= get_settings().recommend_max_parse_retries:
        return "parse"
    return "degrade_end"


def _route_after_filter(state: CookState) -> str:
    if (state.query or "").strip():
        return "retrieve"
    return "degrade_end"


def _route_after_retrieve(state: CookState) -> str:
    if state.candidates:
        return "rank"
    return "end"


def build_graph():
    graph = StateGraph(CookState)
    graph.add_node("parse", parse_node)
    graph.add_node("link", link_node)
    graph.add_node("filter", filter_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("rank", rank_node)
    graph.add_node("generate", generate_node)
    graph.add_node("degrade_end", degrade_end_node)

    graph.add_edge(START, "parse")
    graph.add_conditional_edges(
        "parse",
        _route_after_parse,
        {"parse": "parse", "link": "link", "degrade_end": "degrade_end"},
    )
    graph.add_edge("link", "filter")
    graph.add_conditional_edges(
        "filter",
        _route_after_filter,
        {"retrieve": "retrieve", "degrade_end": "degrade_end"},
    )
    graph.add_conditional_edges(
        "retrieve",
        _route_after_retrieve,
        {"rank": "rank", "end": END},
    )
    graph.add_edge("rank", "generate")
    graph.add_edge("generate", END)
    graph.add_edge("degrade_end", END)

    return graph.compile()
