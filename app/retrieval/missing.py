"""缺料计算：排除调料、可用食材纯精确匹配（name / alias 相等）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy import select

from app.core.html_clean import clean_text
from app.db.session import SessionLocal
from app.models import Ingredient, RecipeIngredient


@dataclass
class MissingInfo:
    essential_total: int
    missing_ingredients: list[str] = field(default_factory=list)


class MissingIngredientsCalculator:
    def __init__(self, session_factory: Callable = SessionLocal) -> None:
        self._session_factory = session_factory

    def for_recipes(
        self, recipe_ids: list[int], available_names: list[str]
    ) -> dict[int, MissingInfo]:
        ids = list(dict.fromkeys(recipe_ids))
        if not ids:
            return {}

        # 缺料计算两查询合并为一次会话（单连接 checkout），
        # 降低并发下的连接往返开销（P5 压测：10 VU 时每查询 ~25-50ms）。
        with self._session_factory() as session:
            dict_rows = session.execute(
                select(Ingredient.id, Ingredient.name, Ingredient.aliases)
            ).all()
            rows = session.execute(
                select(
                    RecipeIngredient.recipe_id,
                    RecipeIngredient.ingredient_id,
                    RecipeIngredient.is_essential,
                    Ingredient.name,
                    Ingredient.category,
                )
                .join(Ingredient, Ingredient.id == RecipeIngredient.ingredient_id)
                .where(RecipeIngredient.recipe_id.in_(ids))
            ).all()

        by_name: dict[str, int] = {}
        by_alias: dict[str, int] = {}
        for ing_id, name, aliases in dict_rows:
            if name:
                by_name.setdefault(name, ing_id)
            for alias in aliases or []:
                if isinstance(alias, str) and alias:
                    by_alias.setdefault(alias, ing_id)

        # 精确匹配：name 相等 → alias 相等 → 未命中名按名称相等兜底
        available_ids: set[int] = set()
        unresolved: set[str] = set()
        for raw in available_names:
            name = clean_text(raw)
            if not name:
                continue
            if name in by_name:
                available_ids.add(by_name[name])
            elif name in by_alias:
                available_ids.add(by_alias[name])
            else:
                unresolved.add(name)

        result: dict[int, MissingInfo] = {}
        for recipe_id in ids:
            essential_total = 0
            missing: list[str] = []
            for rid, ing_id, is_essential, ing_name, category in rows:
                if rid != recipe_id or category == "调料" or not is_essential:
                    continue
                essential_total += 1
                if ing_id not in available_ids and (ing_name or "") not in unresolved:
                    missing.append(ing_name or "")
            result[recipe_id] = MissingInfo(
                essential_total=essential_total, missing_ingredients=missing
            )
        return result
