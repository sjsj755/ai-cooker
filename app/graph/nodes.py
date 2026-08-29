"""LangGraph 节点：parse（LLM 识别）→ link（四级映射）→ filter（query 构造）→
retrieve/rank（复用 P2）→ generate（LLM 推荐 + 降级 MySQL 补全）。

节点返回契约：一律 {**state.model_dump(), ...更新项} 全量展开，
保证 last-value-wins 合并语义下未更新键（含 retry_count）保留。
"""

from __future__ import annotations

import asyncio
import logging
import time
from functools import lru_cache

from sqlalchemy import select

from app.config import get_settings
from app.core.llm import LLMProvider
from app.core.logging import get_logger, log_event
from app.core.openai_llm import LLMConfigError, OpenAICompatibleLLM
from app.core.retriever import RecipeCandidate
from app.db.session import SessionLocal
from app.graph.linking import IngredientLinker, get_ingredient_linker
from app.graph.prompts import generate_prompt, parse_prompt
from app.graph.state import CookState, ParsedIngredient, Recommendation
from app.models import Recipe
from app.retrieval.errors import RetrievalUnavailableError
from app.retrieval.ranking import EMPTY_RESULT_NOTICE, get_ranking_service
from app.schemas.recommend import IngredientExtractionList, RecommendationSet

logger = get_logger("app.graph")

MAX_FILTER_ITEMS = 30
MAX_ITEM_LENGTH = 50
PARSE_FAIL_NOTICE = "未能识别食材，请补充描述"
GENERATE_DEGRADE_NOTICE = "AI 文案不可用，已展示菜谱原文"


def degrade_end_node(state: CookState) -> CookState:
    """降级结束：识别失败/空 query 的统一出口。"""
    return {
        **state.model_dump(),
        "recommendations": [],
        "degraded": True,
        "notice": PARSE_FAIL_NOTICE,
    }


@lru_cache
def get_llm_provider() -> LLMProvider | None:
    """真实 LLM 提供者；无 key 或配置错误返回 None（调用方走降级）。"""
    settings = get_settings()
    if not settings.llm_api_key:
        return None
    try:
        return OpenAICompatibleLLM(settings)
    except LLMConfigError:
        return None


def _retry_cap() -> int:
    return get_settings().recommend_max_parse_retries + 1


async def parse_node(state: CookState) -> CookState:
    """LLM 识别自由文本食材；失败按 retry_count 门控（不自行决定重试）。"""
    started = time.perf_counter()
    retry_count = min(state.retry_count, _retry_cap())
    raw_items = [
        item for item in (state.ingredients or []) if item and item.strip()
    ]
    provider = get_llm_provider()
    if not raw_items or provider is None:
        return {
            **state.model_dump(),
            "parsed_ingredients": [],
            "parse_error": True,
            "retry_count": _retry_cap(),  # 不可恢复：直接超限走降级
        }
    try:
        result = await provider.structured(
            parse_prompt(raw_items), IngredientExtractionList
        )
    except Exception as exc:  # noqa: BLE001 - 解析失败统一走重试门控
        log_event(
            logger,
            logging.WARNING,
            "graph.parse.failed",
            error=f"{type(exc).__name__}: {exc}",
            retry_count=retry_count,
        )
        return {
            **state.model_dump(),
            "parsed_ingredients": [],
            "parse_error": True,
            "retry_count": min(retry_count + 1, _retry_cap()),
        }
    parsed = [
        ParsedIngredient(
            raw_name=item.name,
            quantity=item.quantity,
            unit=item.unit,
        )
        for item in result.items
    ]
    log_event(
        logger,
        logging.INFO,
        "graph.parse.done",
        duration_ms=round((time.perf_counter() - started) * 1000, 1),
        items=len(parsed),
    )
    return {
        **state.model_dump(),
        "parsed_ingredients": parsed,
        "parse_error": False,
        "retry_count": retry_count,
    }


async def link_node(state: CookState) -> CookState:
    """四级映射（精确 → 别名 → 包含 → 向量）到食材字典。"""
    parsed = state.parsed_ingredients or []
    if not parsed:
        return {**state.model_dump(), "parsed_ingredients": []}
    linker: IngredientLinker = get_ingredient_linker()
    try:
        linked = await linker.link(parsed)
    except RetrievalUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001 - MySQL 字典读取失败 → 503
        raise RetrievalUnavailableError(
            f"食材字典映射失败: {type(exc).__name__}: {exc}"
        ) from exc
    return {**state.model_dump(), "parsed_ingredients": linked}


def filter_node(state: CookState) -> CookState:
    """清洗去重 → 数量/长度上限 → 构造 query（标准名优先，未映射用 raw_name）。"""
    parsed = state.parsed_ingredients or []
    if not parsed:
        return {
            **state.model_dump(),
            "query": "",
            "ingredients": [],
            "degraded": True,
            "notice": PARSE_FAIL_NOTICE,
        }
    query_parts: list[str] = []
    normalized: list[str] = []
    seen_query: set[str] = set()
    seen_normalized: set[str] = set()
    for item in parsed[:MAX_FILTER_ITEMS]:
        raw = (item.raw_name or "").strip()
        if not raw or len(raw) > MAX_ITEM_LENGTH:
            continue
        qname = (item.normalized_name or raw).strip()
        if not qname or qname in seen_query:
            continue
        seen_query.add(qname)
        query_parts.append(qname)
        if item.normalized_name and item.ingredient_id is not None:
            norm = item.normalized_name.strip()
            if norm not in seen_normalized:
                seen_normalized.add(norm)
                normalized.append(norm)
    if not query_parts:
        return {
            **state.model_dump(),
            "query": "",
            "ingredients": [],
            "degraded": True,
            "notice": PARSE_FAIL_NOTICE,
        }
    return {
        **state.model_dump(),
        "query": " ".join(query_parts),
        "ingredients": normalized,
    }


async def retrieve_node(state: CookState) -> CookState:
    """P2：以 state.query 为唯一检索文本，ingredients 仅进缺料计算。"""
    query = (state.query or "").strip()
    if not query:
        return {
            **state.model_dump(),
            "candidates": [],
            "degraded": bool(state.degraded),
            "notice": "缺少查询文本",
        }
    service = get_ranking_service()
    try:
        result = await service.rank(
            query,
            available_ingredients=state.ingredients or [],
            exclude_tags=state.exclude_tags or [],
        )
    except RetrievalUnavailableError:
        return {
            **state.model_dump(),
            "candidates": [],
            "degraded": True,
            "notice": "检索服务暂不可用，请稍后重试",
        }
    return {
        **state.model_dump(),
        "candidates": result.recipes,
        "degraded": bool(state.degraded) or result.degraded,
        "notice": result.notice,
    }


async def rank_node(state: CookState) -> CookState:
    """候选已按缺料数/评分/recipe_id 字典序排好，取配置 Top-K。"""
    top_k = get_settings().recommend_top_k
    return {
        **state.model_dump(),
        "ranked": (state.candidates or [])[:top_k],
    }


async def generate_node(state: CookState) -> CookState:
    """LLM 生成推荐；防幻觉（recipe_id 必须在候选集）；降级 MySQL 补全 steps。"""
    started = time.perf_counter()
    ranked = state.ranked or []
    if not ranked:
        log_event(
            logger,
            logging.INFO,
            "graph.generate.done",
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            recommendations=0,
            degraded=True,
        )
        return {
            **state.model_dump(),
            "recommendations": [],
            "notice": EMPTY_RESULT_NOTICE,
        }
    provider = get_llm_provider()
    if provider is None:
        result_state = await _degrade_recommendations(state, ranked)
        log_event(
            logger,
            logging.INFO,
            "graph.generate.done",
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            recommendations=len(result_state["recommendations"]),
            degraded=True,
        )
        return result_state
    try:
        result = await provider.structured(
            generate_prompt(ranked, state.ingredients or [], state.exclude_tags or []),
            RecommendationSet,
        )
    except Exception as exc:  # noqa: BLE001 - 生成失败走降级补全
        log_event(
            logger,
            logging.WARNING,
            "graph.generate.failed",
            error=f"{type(exc).__name__}: {exc}",
            degraded=True,
        )
        result_state = await _degrade_recommendations(state, ranked)
        log_event(
            logger,
            logging.INFO,
            "graph.generate.done",
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            recommendations=len(result_state["recommendations"]),
            degraded=True,
        )
        return result_state
    result_state = await _validate_recommendations(state, result.recommendations)
    log_event(
        logger,
        logging.INFO,
        "graph.generate.done",
        duration_ms=round((time.perf_counter() - started) * 1000, 1),
        recommendations=len(result_state["recommendations"]),
        degraded=bool(result_state["degraded"]),
    )
    return result_state


async def _validate_recommendations(
    state: CookState, recommendations: list[Recommendation]
) -> CookState:
    """防幻觉：白名单 + 去重 + 候选序稳定；数据字段一律回填候选值。

    LLM 只负责 steps/tips 文案；title / match_score / missing_ingredients /
    difficulty / cook_time_minutes 等事实字段以候选集为准，杜绝改写与编造。
    """
    ranked = state.ranked or []
    candidates = {c.recipe_id: c for c in ranked}
    order = {c.recipe_id: i for i, c in enumerate(ranked)}
    seen: set[int] = set()
    valid: list[tuple[RecipeCandidate, Recommendation]] = []
    dropped = 0
    duplicates = 0
    for rec in recommendations:
        candidate = candidates.get(rec.recipe_id)
        if candidate is None:
            dropped += 1
            continue
        if rec.recipe_id in seen:
            duplicates += 1
            continue
        seen.add(rec.recipe_id)
        valid.append((candidate, rec))
    if dropped:
        log_event(
            logger,
            logging.WARNING,
            "graph.generate.hallucination_dropped",
            dropped=dropped,
            total=len(recommendations),
        )
    if duplicates:
        log_event(
            logger,
            logging.WARNING,
            "graph.generate.duplicate_dropped",
            dropped=duplicates,
            total=len(recommendations),
        )
    if not valid:
        return await _degrade_recommendations(state, ranked)
    need_steps_ids = [rec.recipe_id for _, rec in valid if not rec.steps]
    steps_map: dict[int, list[dict] | None] = {}
    if need_steps_ids:
        try:
            steps_map = await asyncio.to_thread(_load_steps, need_steps_ids)
        except Exception as exc:  # noqa: BLE001 - 回填失败即视为检索不可用
            raise RetrievalUnavailableError(
                f"回填菜谱步骤失败: {type(exc).__name__}: {exc}"
            ) from exc
    final: list[Recommendation] = []
    for candidate, rec in sorted(valid, key=lambda pair: order[pair[0].recipe_id]):
        steps = rec.steps
        if not steps:
            steps = steps_map.get(candidate.recipe_id)
            if steps is None:
                raise RetrievalUnavailableError(
                    f"回填菜谱步骤失败: recipe {candidate.recipe_id} 不存在或 steps 为空"
                )
        final.append(
            Recommendation(
                recipe_id=candidate.recipe_id,
                title=candidate.title,
                match_score=candidate.match_score,
                missing_ingredients=candidate.missing_ingredients,
                difficulty=candidate.difficulty,
                cook_time_minutes=candidate.cook_time_minutes,
                steps=steps,
                tips=rec.tips,
            )
        )
    return {
        **state.model_dump(),
        "recommendations": final,
        "degraded": bool(state.degraded),
        "notice": state.notice,
    }


async def _degrade_recommendations(
    state: CookState, ranked: list[RecipeCandidate]
) -> CookState:
    """LLM 不可用/失败 → 一次查 MySQL 构造完整 Recommendation（steps=原文）。"""
    ids = [c.recipe_id for c in ranked]
    try:
        rows = await asyncio.to_thread(_load_recipes, ids)
    except Exception as exc:  # noqa: BLE001 - 降级路径 MySQL 失败 → 503
        raise RetrievalUnavailableError(
            f"降级路径读取菜谱失败: {type(exc).__name__}: {exc}"
        ) from exc
    recommendations: list[Recommendation] = []
    for cand in ranked:
        row = rows.get(cand.recipe_id)
        if row is None:
            continue  # 候选在 MySQL 已不存在：跳过，不返回缺 steps 半成品
        recommendations.append(
            Recommendation(
                recipe_id=cand.recipe_id,
                title=cand.title or row["title"],
                match_score=cand.match_score,
                missing_ingredients=cand.missing_ingredients,
                difficulty=row["difficulty"],
                cook_time_minutes=row["cook_time_minutes"],
                steps=row["steps"],
                tips=None,
            )
        )
    return {
        **state.model_dump(),
        "recommendations": recommendations,
        "degraded": True,
        "notice": GENERATE_DEGRADE_NOTICE,
    }


def _load_recipes(recipe_ids: list[int]) -> dict[int, dict]:
    if not recipe_ids:
        return {}
    with SessionLocal() as session:
        rows = session.execute(
            select(
                Recipe.id,
                Recipe.title,
                Recipe.difficulty,
                Recipe.cook_time_minutes,
                Recipe.steps,
            ).where(Recipe.id.in_(recipe_ids))
        ).all()
    return {
        r.id: {
            "title": r.title,
            "difficulty": r.difficulty,
            "cook_time_minutes": r.cook_time_minutes,
            "steps": r.steps,
        }
        for r in rows
    }


def _load_steps(recipe_ids: list[int]) -> dict[int, list[dict] | None]:
    if not recipe_ids:
        return {}
    with SessionLocal() as session:
        rows = session.execute(
            select(Recipe.id, Recipe.steps).where(Recipe.id.in_(recipe_ids))
        ).all()
    return {r.id: r.steps for r in rows}
