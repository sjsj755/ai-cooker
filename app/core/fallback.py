"""兜底框架：重试（指数退避 + jitter）、DegradedResult、FallbackError。"""

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any, Generic, TypeVar, overload

T = TypeVar("T")


class FallbackError(Exception):
    """兜底框架错误：重试超限或降级流程本身失败。"""


@dataclass
class DegradedResult(Generic[T]):
    """降级结果：data 为可用的部分数据，degraded 标记是否走降级路径。"""

    data: T | None = None
    degraded: bool = False
    notice: str | None = None


def degrade(notice: str, data: T | None = None) -> DegradedResult[T]:
    """构造降级结果（degraded=True + 面向用户的提示）。"""
    return DegradedResult(data=data, degraded=True, notice=notice)


def retry_with_backoff(
    max_attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
):
    """异步重试装饰器：指数退避 + 抖动；超限抛 FallbackError（死循环防护）。"""

    def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001 - 统一兜底，由调用方决定降级
                    last_exc = exc
                    if attempt >= max_attempts:
                        break
                    delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                    await asyncio.sleep(delay + random.uniform(0, delay * 0.2))
            raise FallbackError(
                f"重试 {max_attempts} 次后仍失败: {type(last_exc).__name__}: {last_exc}"
            ) from last_exc

        return wrapper

    return decorator
