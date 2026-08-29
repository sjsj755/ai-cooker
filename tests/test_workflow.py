"""LangGraph：能编译、空状态降级结束、未初始化状态带默认值跑通。"""

import asyncio

from app.graph.state import empty_state
from app.graph.workflow import build_graph


def test_graph_compiles_and_runs_empty_state():
    graph = build_graph()
    result = asyncio.run(graph.ainvoke(empty_state()))
    # 空输入不可恢复：parse 直接超限 → degrade_end
    assert result["retry_count"] == 2
    assert result["degraded"] is True
    assert result["notice"] == "未能识别食材，请补充描述"
    assert result["ingredients"] == []
    assert result["parsed_ingredients"] == []
    assert result["candidates"] == []
    assert result["ranked"] == []
    assert result["recommendations"] == []


def test_graph_runs_uninitialized_state_with_defaults():
    """通道默认：BaseModel 状态 Schema 直接 ainvoke 未初始化输入也不缺键。"""
    graph = build_graph()
    result = asyncio.run(graph.ainvoke({}))
    assert result["retry_count"] == 2  # 默认 0 → 空输入不可恢复超限
    assert result["degraded"] is True
    assert result["notice"] == "未能识别食材，请补充描述"
    assert result["ingredients"] == []
