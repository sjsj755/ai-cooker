"""健康检查响应。"""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    database: str
    chroma: str | None = None
