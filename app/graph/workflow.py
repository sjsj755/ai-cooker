"""build_graph()：注册 6 节点线性空图，验收标准为能编译且空状态跑通。"""

from langgraph.graph import END, START, StateGraph

from app.graph.nodes import (
    filter_node,
    generate_node,
    link_node,
    parse_node,
    rank_node,
    retrieve_node,
)
from app.graph.state import CookState


def build_graph():
    graph = StateGraph(CookState)
    graph.add_node("parse", parse_node)
    graph.add_node("link", link_node)
    graph.add_node("filter", filter_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("rank", rank_node)
    graph.add_node("generate", generate_node)

    graph.add_edge(START, "parse")
    graph.add_edge("parse", "link")
    graph.add_edge("link", "filter")
    graph.add_edge("filter", "retrieve")
    graph.add_edge("retrieve", "rank")
    graph.add_edge("rank", "generate")
    graph.add_edge("generate", END)

    return graph.compile()
