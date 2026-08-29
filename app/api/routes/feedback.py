"""POST /api/feedback：匿名收藏 / 不喜欢（P5 防刷 + 幂等 + 独立限流桶 20/min）。"""

import hashlib
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.config import get_settings
from app.core.logging import get_logger, log_event
from app.core.rate_limit import build_limiter, make_route_limit
from app.models import Recipe, UserFeedback
from app.schemas.feedback import FeedbackIn, FeedbackOut

router = APIRouter()
logger = get_logger("app.api.feedback")
_route_limit = make_route_limit(build_limiter(get_settings()))


def client_fingerprint(request: Request) -> str:
    """SHA-256(IP + FEEDBACK_SALT)：64 位十六进制，不落明文 IP。

    FEEDBACK_SALT 生产由 scripts/start.sh 强校验非空；开发默认空盐仍保持匿名
    （哈希不可逆，攻击者无法从指纹反推 IP）。
    """
    ip = request.client.host if request.client else "unknown"
    salt = get_settings().feedback_salt
    return hashlib.sha256(f"{ip}{salt}".encode("utf-8")).hexdigest()


@router.post("", response_model=FeedbackOut, status_code=200)
@_route_limit(f"{get_settings().rate_limit_feedback_per_minute}/minute")
def create_feedback(
    payload: FeedbackIn,
    request: Request,
    db: Session = Depends(get_db),
) -> FeedbackOut:
    """写反馈：recipe 不存在 404；action 非法 422（schema）；幂等 200 不新增行。"""
    if db.scalar(select(Recipe.id).where(Recipe.id == payload.recipe_id)) is None:
        raise HTTPException(status_code=404, detail="菜谱不存在")
    fingerprint = client_fingerprint(request)
    existing = db.scalar(
        select(UserFeedback).where(
            UserFeedback.recipe_id == payload.recipe_id,
            UserFeedback.client_fingerprint == fingerprint,
            UserFeedback.action == payload.action,
        )
    )
    if existing is not None:
        # 幂等：重复提交返回既有行 id，不新增行
        return FeedbackOut(id=existing.id)
    row = UserFeedback(
        recipe_id=payload.recipe_id,
        client_fingerprint=fingerprint,
        action=payload.action,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        # 并发重复提交兜底：唯一索引冲突 → 回滚后返回既有行
        db.rollback()
        existing = db.scalar(
            select(UserFeedback).where(
                UserFeedback.recipe_id == payload.recipe_id,
                UserFeedback.client_fingerprint == fingerprint,
                UserFeedback.action == payload.action,
            )
        )
        if existing is not None:
            return FeedbackOut(id=existing.id)
        raise
    db.refresh(row)
    log_event(
        logger,
        logging.INFO,
        "feedback.created",
        id=row.id,
        recipe_id=payload.recipe_id,
        action=payload.action,
    )
    return FeedbackOut(id=row.id)
