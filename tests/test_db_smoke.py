"""数据库冒烟：表齐全、utf8mb4、联合主键生效。"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.base import Base
from app.models import Ingredient, Recipe, RecipeIngredient

EXPECTED_TABLES = {
    "recipes",
    "ingredients",
    "recipe_ingredients",
    "tags",
    "recipe_tags",
    "user_feedback",
}


def test_all_tables_registered():
    assert EXPECTED_TABLES.issubset(Base.metadata.tables)


def test_mysql_tables_exist(db_session):
    rows = db_session.execute(
        text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'ai_cooker_test'")
    ).all()
    names = {r[0] for r in rows}
    assert EXPECTED_TABLES.issubset(names)


def test_utf8mb4_default(db_session):
    row = db_session.execute(
        text(
            "SELECT default_character_set_name FROM information_schema.schemata "
            "WHERE schema_name = 'ai_cooker_test'"
        )
    ).one()
    assert row[0] == "utf8mb4"


def test_composite_primary_key_enforced(db_session):
    ingredient = Ingredient(name=f"联测食材{id(object())}")
    recipe = Recipe(title="联测菜谱", source_url=f"https://example.test/{id(object())}")
    db_session.add_all([ingredient, recipe])
    db_session.flush()
    db_session.add(RecipeIngredient(recipe_id=recipe.id, ingredient_id=ingredient.id))
    db_session.flush()
    db_session.add(RecipeIngredient(recipe_id=recipe.id, ingredient_id=ingredient.id))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()
