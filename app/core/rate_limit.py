"""slowapi 限流装配（P5）。

设计（对应 docs/P5_PLAN.md §3.1）：
- ``RATE_LIMIT_ENABLED=false`` 默认关闭，本地 / 测试 / k6 压测不受打扰；
- 桶：recommend 10/min、feedback 20/min、其余默认 100/min（路由级覆盖默认桶）；
- ``RATE_LIMIT_STORAGE=redis`` 时 ``RATE_LIMIT_REDIS_URL`` 必填（config 校验 fail-fast），
  并由 ``app.main`` lifespan 对 Redis 执行 ping() 健康检查，失败即阻止启动；
- 多 worker 下 memory 模式各进程独立计数（部署须知：生产多 worker 必须配 Redis）。

路由限流装饰器在路由模块导入时按当时 Settings 组装；因此“启用限流的进程”必须在
导入 ``app.main`` 之前设置好环境变量（生产经 ``scripts/start.sh``，测试用子进程）。
"""

from __future__ import annotations

from typing import Callable, TypeVar

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import Settings

F = TypeVar("F", bound=Callable)


def build_limiter(settings: Settings) -> Limiter | None:
    """按配置构建 slowapi Limiter；未启用返回 None（路由装饰器退化为原样透传）。"""
    if not settings.rate_limit_enabled:
        return None
    storage_uri = None
    if settings.rate_limit_storage == "redis":
        storage_uri = settings.rate_limit_redis_url
    return Limiter(
        key_func=get_remote_address,
        default_limits=[f"{settings.rate_limit_default_per_minute}/minute"],
        storage_uri=storage_uri,
        strategy="fixed-window",
    )


def make_route_limit(limiter: Limiter | None) -> Callable[[str], Callable[[F], F]]:
    """返回路由限流装饰器工厂；limiter 为 None 时装饰器是 no-op。"""

    def limit(limit_str: str) -> Callable[[F], F]:
        def decorator(func: F) -> F:
            if limiter is None:
                return func
            return limiter.limit(limit_str)(func)

        return decorator

    return limit
