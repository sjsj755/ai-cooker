"""LangSmith 评测消费（P5 §3.9）：eval 脚本 ``--trace`` 的条件包装。

配置 ``LANGSMITH_API_KEY`` 时用 ``langsmith.traceable`` 包装评测函数上传 runs；
无 key 时打印跳过提示并原样返回（与现有“无 key 降级”模式一致），不触发网络。
"""

from __future__ import annotations

import sys
from typing import Callable, TypeVar

from app.config import get_settings

F = TypeVar("F", bound=Callable)


def maybe_trace(func: F, name: str, trace: bool) -> F:
    """按 --trace 与 LANGSMITH_API_KEY 决定是否包装为 LangSmith run。"""
    if not trace:
        return func
    settings = get_settings()
    if not settings.langsmith_api_key:
        print(
            "跳过：未配置 LANGSMITH_API_KEY，--trace 不生效",
            file=sys.stderr,
        )
        return func
    from langsmith import traceable

    return traceable(name=name, run_type="chain")(func)
