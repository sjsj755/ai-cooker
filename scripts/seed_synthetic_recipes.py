"""合成评测菜谱：确定性生成并幂等写入 MySQL（source_url=synthetic://）。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.core.crawler import CrawledIngredient, CrawledRecipe, RecipeCrawler  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models import Recipe  # noqa: E402

MAIN = [
    "土豆", "鸡蛋", "番茄", "牛肉", "青椒", "茄子", "豆腐", "鸡翅",
    "猪肉", "洋葱", "黄瓜", "胡萝卜", "西兰花", "虾仁", "排骨", "鱼",
]
SECONDARY = ["土豆", "鸡蛋", "番茄", "青椒", "洋葱", "胡萝卜", "黄瓜", "豆腐"]
VERBS = ["炒", "炖", "烧", "蒸", "煮"]
STYLES = ["家常", "香辣", "清蒸", "红烧", "醋溜", "蒜蓉"]
STYLE_TAGS = {
    "香辣": "辣",
    "清蒸": "清淡",
    "红烧": "家常菜",
    "醋溜": "家常菜",
    "蒜蓉": "家常菜",
    "家常": "家常菜",
}
COOK_TIMES = [15, 25, 40, 60, 90]


class _SeedCrawler(RecipeCrawler):
    name = "synthetic"

    async def fetch_index(self) -> list[str]:
        return []

    async def parse_page(self, url: str) -> CrawledRecipe:
        raise NotImplementedError


def synthetic_recipe(index: int) -> CrawledRecipe:
    main = MAIN[index % len(MAIN)]
    sec = SECONDARY[(index * 3 + 1) % len(SECONDARY)]
    if sec == main:
        sec = SECONDARY[(index * 3 + 2) % len(SECONDARY)]
    verb = VERBS[(index // len(MAIN)) % len(VERBS)]
    style = STYLES[index % len(STYLES)]
    cook_time = COOK_TIMES[index % len(COOK_TIMES)]
    tags = [STYLE_TAGS[style], "家常菜"]
    if sec == "豆腐" or main == "豆腐":
        tags.append("素菜")
    return CrawledRecipe(
        title=f"{style}{main}{verb}{sec}",
        source_url=f"synthetic://seed-{index:06d}",
        difficulty=index % 3 + 1,
        cook_time_minutes=cook_time,
        servings=2,
        description=f"{style}{main}{verb}{sec}，简单家常，适合日常",
        ingredients=[
            CrawledIngredient(name=main, amount="适量", is_essential=True),
            CrawledIngredient(name=sec, amount="适量", is_essential=True),
        ],
        seasonings=[
            CrawledIngredient(name="盐", amount="适量", is_essential=False),
            CrawledIngredient(name="食用油", amount="少许", is_essential=False),
        ],
        tags=list(dict.fromkeys(tags)),
        steps=[
            {"instruction": f"{main}处理并切配", "minutes": 5},
            {"instruction": f"{style}{verb}至熟透", "minutes": max(5, cook_time - 5)},
        ],
    )


def seed(count: int, reset: bool = False) -> int:
    settings = get_settings()
    setup_logging(settings.log_level)
    crawler = _SeedCrawler()
    saved = 0
    with SessionLocal() as session:
        if reset:
            existing = session.scalars(
                select(Recipe).where(Recipe.source_url.like("synthetic://%"))
            ).all()
            for row in existing:
                session.delete(row)
            session.flush()
            print(f"已重置 {len(existing)} 条合成菜谱")
        for i in range(count):
            recipe = synthetic_recipe(i)
            exists = session.scalar(
                select(Recipe.id).where(Recipe.source_url == recipe.source_url)
            )
            if exists:
                continue
            crawler.save(session, recipe)
            saved += 1
        session.commit()
    print(f"目标 {count} 条，本次新增 {saved} 条合成菜谱")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="写入合成评测菜谱")
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--reset", action="store_true", help="先删除全部 synthetic:// 菜谱")
    args = parser.parse_args(argv)
    return seed(args.count, reset=args.reset)


if __name__ == "__main__":
    sys.exit(main())
