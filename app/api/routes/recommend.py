"""POST /api/recipes/recommend：P3 接入 LangGraph 完整工作流。"""

import logging

from fastapi import APIRouter, HTTPException

from app.core.html_clean import clean_text
from app.core.logging import get_logger, log_event
from app.graph.state import empty_state
from app.graph.workflow import build_graph
from app.retrieval.errors import RetrievalUnavailableError
from app.schemas.recommend import RecommendRequest, RecommendResponse

router = APIRouter()
logger = get_logger("app.api.recommend")


@router.post("/recommend", response_model=RecommendResponse, status_code=200)
async def recommend(payload: RecommendRequest) -> RecommendResponse:
    """推荐：LLM 识别 → 四级映射 → 检索排序 → LLM 生成（可降级直出原文）。"""
    if not any(clean_text(item) for item in payload.ingredients):
        raise HTTPException(status_code=400, detail="食材列表不能为空")
    try:
        result = await build_graph().ainvoke(
            empty_state(
                ingredients=payload.ingredients,
                exclude_tags=payload.exclude_tags,
            )
        )
    except RetrievalUnavailableError as exc:
        log_event(
            logger,
            logging.ERROR,
            "recommend.failed",
            error=str(exc),
            http_status=503,
        )
        raise HTTPException(
            status_code=503, detail="推荐服务暂不可用，请稍后重试"
        ) from exc
    except Exception as exc:  # noqa: BLE001 - 未预期异常落 ERROR 日志后返回 500
        log_event(
            logger,
            logging.ERROR,
            "recommend.failed",
            error=f"{type(exc).__name__}: {exc}",
            http_status=500,
        )
        raise HTTPException(status_code=500, detail="推荐服务异常") from exc
    return RecommendResponse(
        recipes=result.get("recommendations") or [],
        degraded=bool(result.get("degraded", False)),
        notice=result.get("notice"),
    )
