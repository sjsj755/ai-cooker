"""FastAPI 应用工厂 + 全局路由注册。"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version=settings.version,
        docs_url="/docs",
        openapi_url="/openapi.json",
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
