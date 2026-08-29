"""健康检查：/health（兼容）、/health/live、/health/ready（DB + Chroma）。"""

from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.config import get_settings
from app.schemas.health import HealthResponse
from app.vector_store import get_chroma_client

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(db: Session = Depends(get_db)) -> HealthResponse:
    return _db_status(db)


@router.get("/health/live", response_model=HealthResponse)
def health_live() -> HealthResponse:
    """存活探针：恒 200，不依赖任何外部组件。"""
    return HealthResponse(status="ok", database="ok", chroma="ok")


@router.get("/health/ready", response_model=HealthResponse)
def health_ready(
    db: Session = Depends(get_db),
    response: Response = None,  # type: ignore[assignment] - FastAPI 注入
) -> HealthResponse:
    """就绪探针：DB + Chroma 连通；任一故障返回 503。"""
    db_ok = _check_db(db)
    chroma_ok = True
    try:
        get_chroma_client(get_settings().chroma_dir).heartbeat()
    except Exception:  # noqa: BLE001 - 健康检查不向上抛
        chroma_ok = False
    ready = db_ok and chroma_ok
    if not ready:
        response.status_code = 503
    return HealthResponse(
        status="ok" if ready else "degraded",
        database="ok" if db_ok else "error",
        chroma="ok" if chroma_ok else "error",
    )


def _db_status(db: Session) -> HealthResponse:
    db_ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - 健康检查不向上抛，返回降级状态
        db_ok = False
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        database="ok" if db_ok else "error",
    )


def _check_db(db: Session) -> bool:
    try:
        db.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False
