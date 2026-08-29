"""RecipeCrawler 采集器基类：清洗、去重、入库骨架。"""

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Ingredient, Recipe, RecipeIngredient, RecipeTag, Tag


class CrawledIngredient(BaseModel):
    name: str
    amount: str | None = None
    is_essential: bool = True


class CrawledRecipe(BaseModel):
    title: str
    source_url: str
    difficulty: int | None = None
    cook_time_minutes: int | None = None
    servings: int | None = None
    steps: list[dict[str, Any]] = Field(default_factory=list)
    description: str | None = None
    ingredients: list[CrawledIngredient] = Field(default_factory=list)
    seasonings: list[CrawledIngredient] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class RecipeCrawler(ABC):
    """每站点一个适配器：实现 fetch_index / parse_page，复用 save() 幂等入库。"""

    name: str

    @abstractmethod
    async def fetch_index(self) -> list[str]:
        """返回待解析的菜谱 URL 列表。"""
        raise NotImplementedError

    @abstractmethod
    async def parse_page(self, url: str) -> CrawledRecipe:
        """解析单页为结构化菜谱。"""
        raise NotImplementedError

    def save(self, session: Session, recipe: CrawledRecipe) -> Recipe:
        """按 source_url 幂等入库：已存在则跳过（去重）。"""
        existing = session.scalar(
            select(Recipe).where(Recipe.source_url == recipe.source_url)
        )
        if existing is not None:
            return existing

        row = Recipe(
            title=recipe.title,
            source_url=recipe.source_url,
            difficulty=recipe.difficulty,
            cook_time_minutes=recipe.cook_time_minutes,
            servings=recipe.servings,
            steps=recipe.steps,
            description=recipe.description,
        )
        session.add(row)
        session.flush()  # 取得 recipe.id

        # 按名称归并（旧数据可能含重复调料，避免 recipe_ingredients 组合主键冲突）
        merged: dict[str, dict[str, Any]] = {}
        for item, category in [
            *((i, None) for i in recipe.ingredients),
            *((s, "调料") for s in recipe.seasonings),
        ]:
            key = item.name.strip()
            if not key:
                continue
            if key in merged:
                entry = merged[key]
                amounts = [a for a in (entry["amount"], item.amount) if a]
                entry["amount"] = "、".join(amounts) if amounts else None
                if category:
                    entry["category"] = category
            else:
                merged[key] = {
                    "name": item.name,
                    "amount": item.amount,
                    "category": category,
                    "is_essential": item.is_essential,
                }

        for entry in merged.values():
            ingredient = self._get_or_create_ingredient(
                session, entry["name"], category=entry["category"]
            )
            session.add(
                RecipeIngredient(
                    recipe_id=row.id,
                    ingredient_id=ingredient.id,
                    amount=entry["amount"],
                    is_essential=entry["is_essential"],
                )
            )

        for tag_name in recipe.tags:
            tag = self._get_or_create_tag(session, tag_name)
            session.add(RecipeTag(recipe_id=row.id, tag_id=tag.id))

        return row

    @staticmethod
    def _get_or_create_ingredient(
        session: Session,
        name: str,
        category: str | None = None,
    ) -> Ingredient:
        """仅新建行写入 category；已存在行保持原值（如种子 葱/姜/蒜=调味）。"""
        ingredient = session.scalar(select(Ingredient).where(Ingredient.name == name))
        if ingredient is None:
            ingredient = Ingredient(name=name, category=category)
            session.add(ingredient)
            session.flush()
        return ingredient

    @staticmethod
    def _get_or_create_tag(session: Session, name: str) -> Tag:
        tag = session.scalar(select(Tag).where(Tag.name == name))
        if tag is None:
            tag = Tag(name=name, kind="unknown")
            session.add(tag)
            session.flush()
        return tag
