"""GET /api/tags：忌口 / 口味标签列表。"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models import Tag
from app.schemas.tags import TagOut

router = APIRouter()


@router.get("", response_model=list[TagOut])
def list_tags(
    kind: str | None = Query(default=None, description="按 kind 过滤（过敏原/忌口/菜系/口味）"),
    db: Session = Depends(get_db),
) -> list[TagOut]:
    stmt = select(Tag).order_by(Tag.kind, Tag.name)
    if kind:
        stmt = stmt.where(Tag.kind == kind)
    rows = db.scalars(stmt).all()
    return [TagOut.model_validate(r) for r in rows]
