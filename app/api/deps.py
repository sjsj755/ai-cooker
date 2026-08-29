"""FastAPI 依赖。"""

from app.db.session import get_db

__all__ = ["get_db"]
