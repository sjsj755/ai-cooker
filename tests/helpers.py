"""测试共享的 DB 插入/清理工具（写入 ai_cooker_test）。"""

from sqlalchemy import select

from app.core.crawler import CrawledIngredient, CrawledRecipe, RecipeCrawler
from app.db.session import SessionLocal
from app.models import Recipe


class _TestCrawler(RecipeCrawler):
    name = "test"

    async def fetch_index(self) -> list[str]:
        return []

    async def parse_page(self, url: str) -> CrawledRecipe:
        raise NotImplementedError


def add_recipe(
    title: str,
    url: str,
    *,
    ingredients=(),
    seasonings=(),
    tags=(),
    difficulty: int | None = 1,
    cook_time: int | None = 20,
    description: str = "测试描述",
) -> int:
    with SessionLocal() as session:
        _TestCrawler().save(
            session,
            CrawledRecipe(
                title=title,
                source_url=url,
                difficulty=difficulty,
                cook_time_minutes=cook_time,
                servings=2,
                steps=[],
                description=description,
                ingredients=[
                    CrawledIngredient(name=n, is_essential=True) for n in ingredients
                ],
                seasonings=[
                    CrawledIngredient(name=n, is_essential=False) for n in seasonings
                ],
                tags=list(tags),
            ),
        )
        session.commit()
    with SessionLocal() as session:
        row = session.scalar(select(Recipe.id).where(Recipe.source_url == url))
        assert row is not None
        return row


def delete_recipe(url: str) -> None:
    with SessionLocal() as session:
        row = session.scalar(select(Recipe).where(Recipe.source_url == url))
        if row is not None:
            session.delete(row)
            session.commit()
