"""测试共享的 DB 插入/清理工具（写入 ai_cooker_test）与可编排 FakeLLM。"""

import asyncio

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


def _as_crawled(items) -> list[CrawledIngredient]:
    """str 或 (name, amount) 元组归一为 CrawledIngredient（amount 可选）。"""
    result: list[CrawledIngredient] = []
    for item in items or ():
        if isinstance(item, str):
            result.append(CrawledIngredient(name=item))
        else:
            result.append(CrawledIngredient(name=item[0], amount=item[1]))
    return result


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
    steps: list[dict] | None = None,
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
                steps=steps if steps is not None else [],
                description=description,
                ingredients=[
                    CrawledIngredient(name=i.name, amount=i.amount, is_essential=True)
                    for i in _as_crawled(ingredients)
                ],
                seasonings=[
                    CrawledIngredient(name=i.name, amount=i.amount, is_essential=False)
                    for i in _as_crawled(seasonings)
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


class FakeLLM:
    """可编排 LLM 假实现：按 schema 名称返回 parse/generate 结果或抛错。

    fail_parse: parse 连续失败次数（>0 时先失败 N 次再成功）；
    fail_generate: generate 一律抛错（触发降级补全路径）；
    latency: 每次调用延迟秒数（性能基线模拟）。
    """

    def __init__(
        self,
        *,
        parse_items=(),
        recommendation_set=None,
        fail_parse: int = 0,
        fail_generate: bool = False,
        latency: float = 0.0,
    ) -> None:
        from app.schemas.recommend import (
            IngredientExtraction,
            IngredientExtractionList,
            RecommendationSet,
        )

        self._extraction_cls = IngredientExtraction
        self._extraction_list_cls = IngredientExtractionList
        self._set_cls = RecommendationSet
        self.parse_items = parse_items
        self.recommendation_set = recommendation_set
        self.fail_parse = fail_parse
        self.fail_generate = fail_generate
        self.latency = latency
        self.parse_calls = 0
        self.generate_calls = 0
        self.prompts: list[str] = []

    async def structured(self, prompt: str, schema):
        if self.latency:
            await asyncio.sleep(self.latency)
        self.prompts.append(prompt)
        if schema is self._extraction_list_cls:
            self.parse_calls += 1
            if self.parse_calls <= self.fail_parse:
                raise RuntimeError("llm down")
            return self._extraction_list_cls(
                items=[
                    self._extraction_cls(
                        name=item[0],
                        quantity=item[1] if len(item) > 1 else None,
                        unit=item[2] if len(item) > 2 else None,
                    )
                    for item in self.parse_items
                ]
            )
        if schema is self._set_cls:
            self.generate_calls += 1
            if self.fail_generate:
                raise RuntimeError("llm down")
            if self.recommendation_set is None:
                raise AssertionError("FakeLLM 未配置 recommendation_set")
            return self.recommendation_set
        raise AssertionError(f"unexpected schema: {schema}")
