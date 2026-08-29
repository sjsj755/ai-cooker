"""种子数据：可查询、幂等。"""

from sqlalchemy import func, select

from app.models import Ingredient, Tag
from scripts.seed_dictionary import seed


def test_seed_idempotent(db_session):
    before_ingredients = db_session.scalar(select(func.count()).select_from(Ingredient))
    before_tags = db_session.scalar(select(func.count()).select_from(Tag))

    stats = seed(db_session)

    assert stats["ingredients"]["created"] == 0
    assert stats["tags"]["created"] == 0
    after_ingredients = db_session.scalar(select(func.count()).select_from(Ingredient))
    after_tags = db_session.scalar(select(func.count()).select_from(Tag))
    assert after_ingredients == before_ingredients
    assert after_tags == before_tags


def test_seed_data_queryable(db_session):
    # 中文 LIKE“土”→ 土豆
    rows = db_session.scalars(select(Ingredient).where(Ingredient.name.like("%土%"))).all()
    assert any(r.name == "土豆" for r in rows)
    # 别名命中
    potato = db_session.scalar(select(Ingredient).where(Ingredient.name == "土豆"))
    assert potato is not None
    assert "马铃薯" in (potato.aliases or [])


def test_tags_seeded(db_session):
    names = set(db_session.scalars(select(Tag.name)).all())
    assert {"海鲜", "辣", "素食", "坚果", "乳制品"}.issubset(names)
