"""engine / SessionLocal / get_db 依赖。"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

engine = create_engine(
    get_settings().database_url,
    pool_pre_ping=True,
    pool_recycle=3600,
    # P5 压测：10 并发 VU 场景下默认 pool_size=5 会排队拖高 P95；
    # 池大小覆盖 10 VU + 20 溢出，生产多 worker 按 worker 数评估上限。
    pool_size=10,
    max_overflow=20,
    echo=False,
)
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：请求级会话，用完即关。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
