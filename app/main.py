"""FastAPI 应用工厂 + 全局路由注册 + 限流装配（P5）。"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded

from app.api.router import api_router
from app.config import get_settings
from app.core.logging import get_logger, log_event
from app.core.rate_limit import build_limiter

logger = get_logger("app.main")

RATE_LIMIT_MESSAGE = "请求过于频繁，请稍后重试"


def _rate_limit_exceeded_handler(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    """429 统一友好 JSON（slowapi 默认英文错误体不对外透出）。"""
    return JSONResponse(status_code=429, content={"detail": RATE_LIMIT_MESSAGE})


async def _redis_ping_fail_fast(url: str) -> None:
    """Redis 健康检查：失败抛异常阻止应用启动（而非降级）。

    slowapi 不原生支持 fail-fast 配置校验，故在 lifespan 启动阶段主动 ping；
    失败即抛异常，由 FastAPI 启动流程向上传播，进程直接退出。
    """
    import redis.asyncio as aioredis

    client = aioredis.from_url(
        url,
        socket_connect_timeout=2.0,
        socket_timeout=2.0,
    )
    try:
        await client.ping()
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "startup.redis_ping_failed",
            error=f"{type(exc).__name__}: {exc}",
        )
        raise RuntimeError(
            f"Redis 健康检查失败，拒绝启动：{type(exc).__name__}: {exc}"
        ) from exc
    finally:
        await client.aclose()


@asynccontextmanager
async def lifespan(application: FastAPI):
    """启动阶段：Redis fail-fast 健康检查（仅限流启用且 storage=redis 时）。"""
    settings = get_settings()
    if settings.rate_limit_enabled and settings.rate_limit_storage == "redis":
        await _redis_ping_fail_fast(settings.rate_limit_redis_url)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    limiter = build_limiter(settings)
    application = FastAPI(
        title=settings.app_name,
        version=settings.version,
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    if limiter is not None:
        # 路由模块在导入时已按同一 Settings 应用了 @limiter.limit 装饰器；
        # 这里挂载 Limiter 实例与统一 429 处理器（slowapi 约定）。
        application.state.limiter = limiter
        application.add_exception_handler(
            RateLimitExceeded, _rate_limit_exceeded_handler
        )
    # CORS 默认关闭；仅配置了白名单才开启
    if settings.cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    application.include_router(api_router)
    # P4：同源托管静态前端；必须置于 include_router 之后，
    # 保证 /api/*、/docs、/openapi.json 优先匹配，未知静态路径返回 404。
    application.mount(
        "/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend"
    )
    return application


app = create_app()
