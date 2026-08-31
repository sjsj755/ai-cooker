"""FastAPI 应用工厂 + 全局路由注册 + 限流装配（P5）+ 安全加固（P6）。"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from starlette.datastructures import MutableHeaders

from app.api.router import api_router
from app.config import get_settings
from app.core.logging import get_logger, log_event
from app.core.net_clients import close_http_clients
from app.core.rate_limit import build_limiter

logger = get_logger("app.main")

RATE_LIMIT_MESSAGE = "请求过于频繁，请稍后重试"


class SecurityHeadersMiddleware:
    """P6 安全响应头：CSP / X-Frame-Options / X-Content-Type-Options / Referrer-Policy。

    CSP 指令按前端静态扫描结果校准（同源脚本/样式、无外链、无内联 style），
    禁止外部域加载；直连 app 端口同样生效，Caddy 不重复设置。
    """

    HEADERS = {
        "Content-Security-Policy": "default-src 'self'",
        "X-Frame-Options": "DENY",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for key, value in self.HEADERS.items():
                    headers.append(key, value)
            await send(message)

        await self.app(scope, receive, send_wrapper)


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


async def _startup_warmup() -> None:
    """P6.1 后台预热检索（BM25 语料 + Chroma），失败仅告警不阻断启动。"""
    from app.retrieval.ranking import get_ranking_service

    try:
        await get_ranking_service().warmup()
        log_event(logger, logging.INFO, "startup.warmup.done")
    except Exception as exc:  # noqa: BLE001 - 预热为尽力而为，失败不阻止服务
        log_event(
            logger,
            logging.WARNING,
            "startup.warmup.failed",
            error=f"{type(exc).__name__}: {exc}",
        )


@asynccontextmanager
async def lifespan(application: FastAPI):
    """启动阶段：Redis fail-fast 健康检查（仅限流启用且 storage=redis 时）。"""
    settings = get_settings()
    if settings.rate_limit_enabled and settings.rate_limit_storage == "redis":
        await _redis_ping_fail_fast(settings.rate_limit_redis_url)
    warmup_task: asyncio.Task | None = None
    if settings.warmup_on_startup:
        warmup_task = asyncio.create_task(_startup_warmup())
        application.state.warmup_task = warmup_task
        # P6.4：有限等待预热完成再接受请求（默认 ≤10s；BM25 落盘加载约 1-2s），
        # 超时转后台继续（shield 保证不被取消），失败仅告警语义不变
        try:
            await asyncio.wait_for(
                asyncio.shield(warmup_task),
                timeout=max(settings.warmup_wait_seconds, 0.0),
            )
        except asyncio.TimeoutError:
            log_event(
                logger,
                logging.INFO,
                "startup.warmup.background",
                wait_seconds=settings.warmup_wait_seconds,
            )
        except Exception:  # noqa: BLE001 - 预热失败不阻断启动
            pass
    yield
    if warmup_task is not None:
        try:
            await asyncio.wait_for(warmup_task, timeout=5.0)
        except asyncio.TimeoutError:
            warmup_task.cancel()
        except Exception:  # noqa: BLE001 - 关停阶段不因预热异常报错
            pass
    await close_http_clients()


def create_app() -> FastAPI:
    settings = get_settings()
    limiter = build_limiter(settings)
    docs_enabled = settings.docs_enabled
    application = FastAPI(
        title=settings.app_name,
        version=settings.version,
        docs_url="/docs" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
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
    # P6：Host 白名单（逗号分隔；空 = 不校验）。TrustedHostMiddleware 返回 400，
    # 防 Host 头注入 / 缓存投毒。
    if settings.allowed_hosts.strip():
        allowed_hosts = [
            host.strip()
            for host in settings.allowed_hosts.split(",")
            if host.strip()
        ]
        if allowed_hosts:
            application.add_middleware(
                TrustedHostMiddleware, allowed_hosts=allowed_hosts
            )
    # P6：安全响应头（默认开启；测试 / 个别场景可显式关闭）
    if settings.security_headers_enabled:
        application.add_middleware(SecurityHeadersMiddleware)
    application.include_router(api_router)
    # P4：同源托管静态前端；必须置于 include_router 之后，
    # 保证 /api/*、/docs、/openapi.json 优先匹配，未知静态路径返回 404。
    application.mount(
        "/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend"
    )
    return application


app = create_app()
