"""POST /api/recipes/recommend：P3 接入 LangGraph 完整工作流（P5 限流桶 10/min）。

P6.4 快路径：缓存未命中时先以 fast_first=True 跑图，秒级返回 MySQL 原文
（ai_pending=true）；随后按缓存键单飞触发后台任务补全 AI 文案并写长缓存，
前端轮询 POST /api/recipes/recommend/status 感知完成。
"""

import asyncio
import logging
import time

from fastapi import APIRouter, HTTPException, Request

from app.core.html_clean import clean_text
from app.core.logging import get_logger, log_event
from app.core.rate_limit import build_limiter, make_route_limit
from app.core.ttl_cache import TTLCache
from app.config import get_settings
from app.graph.nodes import generate_node
from app.graph.state import CookState, empty_state
from app.graph.workflow import build_graph
from app.retrieval.errors import RetrievalUnavailableError
from app.schemas.recommend import (
    RecommendRequest,
    RecommendResponse,
    RecommendStatusResponse,
)

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
# P6.4 后台 AI 补全状态（进程内存，单 worker 语义）：
# - _ai_warm_tasks：在飞任务（缓存键 → Task），单飞去重；
# - _ai_warm_failed_at：最近失败时间戳（monotonic），用于让前端提前停止轮询。
_ai_warm_tasks: dict[tuple, asyncio.Task] = {}
_ai_warm_failed_at: dict[tuple, float] = {}


def _ai_warm_failed_ttl() -> float:
    """失败标记 TTL = 降级缓存 TTL（最小 1s），与快响应生命周期对齐。"""
    return max(float(_settings.recommend_cache_degraded_ttl_seconds), 1.0)


def _prune_failed_marks() -> None:
    """惰性清理过期失败标记（读取/写入前调用）。"""
    now = time.monotonic()
    ttl = _ai_warm_failed_ttl()
    expired = [k for k, ts in _ai_warm_failed_at.items() if now - ts > ttl]
    for key in expired:
        _ai_warm_failed_at.pop(key, None)


def _warm_failed_recently(key: tuple) -> bool:
    """失败标记是否新鲜（≤ TTL）；过期视为不存在并惰性清除。"""
    ts = _ai_warm_failed_at.get(key)
    if ts is None:
        return False
    if time.monotonic() - ts > _ai_warm_failed_ttl():
        _ai_warm_failed_at.pop(key, None)
        return False
    return True


def _record_ai_warm_failure(key: tuple) -> None:
    """记录失败时间戳：先写标记、再由任务 finally 移出在飞集合，
    杜绝“不在飞 + 无标记 + 降级缓存仍存活”的空窗误判为 warming=true。"""
    _prune_failed_marks()
    _ai_warm_failed_at[key] = time.monotonic()
    if len(_ai_warm_failed_at) > int(_settings.recommend_cache_max_entries):
        oldest = min(_ai_warm_failed_at, key=_ai_warm_failed_at.get)
        _ai_warm_failed_at.pop(oldest, None)


async def _warm_ai_task(key: tuple, fast_state: dict) -> None:
    """后台补全 AI 文案：复用快路径已算好的 ranked，只跑 generate 节点；
    成功写长缓存（非降级），失败/降级仅记录失败标记，不污染长缓存。"""
    try:
        full_state = await generate_node(
            CookState(
                **{
                    **fast_state,
                    "fast_first": False,
                    "recommendations": [],
                    "degraded": False,
                    "notice": None,
                    "ai_pending": False,
                }
            )
        )
        if full_state.get("degraded"):
            log_event(
                logger,
                logging.WARNING,
                "recommend.ai_warm.degraded",
                notice=full_state.get("notice"),
            )
            _record_ai_warm_failure(key)
            return
        response = RecommendResponse(
            recipes=full_state.get("recommendations") or [],
            degraded=False,
            notice=full_state.get("notice"),
        )
        if _recommend_cache.enabled:
            _recommend_cache.set(key, response)
        log_event(
            logger,
            logging.INFO,
            "recommend.ai_warm.done",
            recommendations=len(response.recipes),
        )
    except Exception as exc:  # noqa: BLE001 - 后台失败仅告警，不阻断已返回的快响应
        log_event(
            logger,
            logging.WARNING,
            "recommend.ai_warm.failed",
            error=f"{type(exc).__name__}: {exc}",
        )
        _record_ai_warm_failure(key)
    finally:
        _ai_warm_tasks.pop(key, None)


def _spawn_ai_warm(key: tuple, fast_state: dict) -> None:
    """单飞启动后台补全；新任务先清除旧失败标记（新尝试取代旧失败）。"""
    if key in _ai_warm_tasks:
        return
    _ai_warm_failed_at.pop(key, None)
    _prune_failed_marks()
    task = asyncio.create_task(_warm_ai_task(key, fast_state))
    _ai_warm_tasks[key] = task


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
        # P6.4 快路径：跳过 generate LLM，秒级返回 MySQL 原文；AI 文案后台补全
        result = await build_graph().ainvoke(
            empty_state(
                ingredients=payload.ingredients,
                exclude_tags=payload.exclude_tags,
                fast_first=bool(_settings.recommend_fast_first_enabled),
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
        ai_pending=bool(result.get("ai_pending", False)),
    )
    # 非降级结果进长 TTL 缓存；降级结果进短 TTL 缓存（LLM 拥堵时重复查询也秒回，
    # 且恢复后最多 TTL 内即返回新结果，不长期“粘住”）
    if not response.degraded and _recommend_cache.enabled:
        _recommend_cache.set(key, response)
    elif response.degraded and _degraded_cache.enabled:
        _degraded_cache.set(key, response)
    # 快路径 + 有候选 → 后台补全 AI 文案（单飞）
    if response.ai_pending and result.get("ranked"):
        _spawn_ai_warm(key, result)
    return response


@router.post("/recommend/status", response_model=RecommendStatusResponse, status_code=200)
@_route_limit(f"{get_settings().rate_limit_status_per_minute}/minute")
async def recommend_status(
    payload: RecommendRequest, request: Request
) -> RecommendStatusResponse:
    """P6.4 状态轮询：只做缓存查询，AI 文案就绪即携带完整结果（深拷贝）。

    判定优先级：长缓存命中 → ready；在飞 → warming；近期失败 → 停止轮询；
    仅降级快响应 → warming；均无 → 停止轮询。
    """
    key = _cache_key(payload)
    if _recommend_cache.enabled:
        cached = _recommend_cache.get(key)
        if cached is not None:
            return RecommendStatusResponse(
                ready=True, result=cached.model_copy(deep=True)
            )
    if key in _ai_warm_tasks:
        return RecommendStatusResponse(ready=False, warming=True)
    if _warm_failed_recently(key):
        return RecommendStatusResponse(ready=False, warming=False)
    if _degraded_cache.enabled and _degraded_cache.get(key) is not None:
        return RecommendStatusResponse(ready=False, warming=True)
    return RecommendStatusResponse(ready=False, warming=False)
