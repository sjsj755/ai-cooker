"""LangGraph：能编译、空状态跑通（无 query 时 retrieve 置缺查询提示）。"""

import asyncio

from app.graph.state import empty_state
from app.graph.workflow import build_graph


def test_graph_compiles_and_runs_empty_state():
    graph = build_graph()
    result = asyncio.run(graph.ainvoke(empty_state()))
    assert result["retry_count"] == 0
    assert result["degraded"] is False
    assert result["notice"] == "缺少查询文本"
    assert result["ingredients"] == []
    assert result["candidates"] == []
    assert result["ranked"] == []
