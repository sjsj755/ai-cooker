"""POST /api/recipes/recommend：P3 接入 LangGraph 完整工作流（P5 限流桶 10/min）。"""

import logging

from fastapi import APIRouter, HTTPException, Request

from app.core.html_clean import clean_text
from app.core.logging import get_logger, log_event
from app.core.rate_limit import build_limiter, make_route_limit
from app.core.ttl_cache import TTLCache
from app.config import get_settings
from app.graph.state import empty_state
from app.graph.workflow import build_graph
from app.retrieval.errors import RetrievalUnavailableError
from app.schemas.recommend import RecommendRequest, RecommendResponse

router = APIRouter()
logger = get_logger("app.api.recommend")
_route_limit = make_route_limit(build_limiter(get_settings()))
_settings = get_settings()
_recommend_cache = TTLCache(
    ttl_seconds=max(float(_settings.recommend_cache_ttl_seconds), 0.0),
    max_entries=int(_settings.recommend_cache_max_entries),
)
_degraded_cache = TTLCache(
    ttl_seconds=max(float(_settings.recommend_cache_degraded_ttl_seconds), 0.0),
    max_entries=int(_settings.recommend_cache_max_entries),
)


def _cache_key(payload: RecommendRequest) -> tuple:
    """缓存键：归一化（清洗 + 去重 + 排序）后的食材与忌口，顺序无关。"""
    ingredients = tuple(
        sorted(
            {
                clean_text(item)
                for item in payload.ingredients
                if clean_text(item)
            }
        )
    )
    exclude_tags = tuple(
        sorted(
            {
                clean_text(item)
                for item in (payload.exclude_tags or [])
                if clean_text(item)
            }
        )
    )
    return (ingredients, exclude_tags)


@router.post("/recommend", response_model=RecommendResponse, status_code=200)
@_route_limit(f"{get_settings().rate_limit_recommend_per_minute}/minute")
async def recommend(
    payload: RecommendRequest, request: Request
) -> RecommendResponse:
    """推荐：LLM 识别 → 四级映射 → 检索排序 → LLM 生成（可降级直出原文）。"""
    if not any(clean_text(item) for item in payload.ingredients):
        raise HTTPException(status_code=400, detail="食材列表不能为空")
    key = _cache_key(payload)
    if _recommend_cache.enabled or _degraded_cache.enabled:
        cached = _recommend_cache.get(key) or _degraded_cache.get(key)
        if cached is not None:
            return cached.model_copy(deep=True)
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
    response = RecommendResponse(
        recipes=result.get("recommendations") or [],
        degraded=bool(result.get("degraded", False)),
        notice=result.get("notice"),
    )
    # 非降级结果进长 TTL 缓存；降级结果进短 TTL 缓存（LLM 拥堵时重复查询也秒回，
    # 且恢复后最多 TTL 内即返回新结果，不长期“粘住”）
    if not response.degraded and _recommend_cache.enabled:
        _recommend_cache.set(key, response)
    elif response.degraded and _degraded_cache.enabled:
        _degraded_cache.set(key, response)
    return response
