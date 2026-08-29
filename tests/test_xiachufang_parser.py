"""下厨房适配器解析测试：基于真实 fixture，离线可跑。"""

import asyncio
from pathlib import Path

import httpx
import pytest

from app.config import get_settings
from app.core.crawler import CrawledIngredient
from app.crawlers.xiachufang import (
    PageParseError,
    XiaChuFangCrawler,
    parse_explore_index,
    parse_mobile_category_index,
    parse_sitemap,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8", errors="replace")


def _crawler() -> XiaChuFangCrawler:
    return XiaChuFangCrawler(get_settings())


def test_pc_recipe_parse():
    recipe = _crawler().parse_recipe_html(
        "https://www.xiachufang.com/recipe/104100931/",
        _load("xiachufang_recipe.html"),
    )
    assert recipe.title == "稀碎土豆丝"
    assert [i.name for i in recipe.ingredients] == ["土豆", "青椒"]
    assert [s.name for s in recipe.seasonings] == ["食用油", "盐"]
    assert recipe.seasonings[0].amount == "稍微放多一点点"
    assert recipe.seasonings[0].is_essential is False
    assert len(recipe.steps) == 2
    assert all(s["minutes"] is None for s in recipe.steps)
    assert recipe.tags == ["素菜", "家常菜"]
    assert recipe.description
    assert recipe.source_url == "https://www.xiachufang.com/recipe/104100931/"


def test_mobile_recipe_parse():
    recipe = _crawler().parse_recipe_html(
        "https://m.xiachufang.com/recipe/107802306/",
        _load("xiachufang_m_recipe.html"),
    )
    assert recipe.title == "牛油果生椰抹茶"
    assert [i.name for i in recipe.ingredients] == ["抹茶粉", "牛油果", "厚椰乳"]
    assert recipe.seasonings == []
    assert len(recipe.steps) == 5
    assert recipe.steps[0]["instruction"] == "牛油果在可以不用力就把果蒂摘掉的时候就熟了"
    assert "步骤" not in recipe.steps[0]["instruction"]
    assert recipe.tags == ["下午茶"]
    assert recipe.source_url == "https://m.xiachufang.com/recipe/107802306/"


def test_explore_index():
    urls, has_next = parse_explore_index(_load("xiachufang_index.html"))
    assert len(urls) == 25
    assert has_next is True
    assert all(u.startswith("https://www.xiachufang.com/recipe/") for u in urls)
    assert len(set(urls)) == len(urls)


def test_mobile_category_index():
    urls = parse_mobile_category_index(
        _load("xiachufang_m_index.html"),
        base_url="https://m.xiachufang.com/category/40076/",
    )
    assert len(urls) == 19
    assert all(u.startswith("https://m.xiachufang.com/recipe/") for u in urls)


def test_sitemap_parse():
    xml = (
        '<?xml version="1.0"?><urlset '
        'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url>'
        "<loc>https://www.xiachufang.com/recipe/100/</loc></url></urlset>"
    )
    assert parse_sitemap(xml) == ["https://www.xiachufang.com/recipe/100/"]


def test_rejects_captcha_page():
    html = (
        "<html><head><title>人机验证</title></head>"
        "<body><div>请完成验证后继续访问</div></body></html>"
    )
    with pytest.raises(PageParseError):
        _crawler().parse_recipe_html(
            "https://www.xiachufang.com/recipe/104100931/",
            html,
        )


def test_seasoning_alias_dedup():
    ingredients, seasonings = XiaChuFangCrawler._split(
        [
            CrawledIngredient(name="生抽", amount="1勺"),
            CrawledIngredient(name="老抽", amount="半勺"),
            CrawledIngredient(name="土豆"),
        ]
    )
    assert [i.name for i in ingredients] == ["土豆"]
    assert [(s.name, s.amount) for s in seasonings] == [("酱油", "1勺、半勺")]
    assert all(s.is_essential is False for s in seasonings)


def test_mobile_fallback_on_anti_bot():
    recipe_html = _load("xiachufang_m_recipe.html")

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://www.xiachufang.com/recipe/104100931/":
            return httpx.Response(
                302,
                headers={
                    "Location": (
                        "https://www.xiachufang.com/auth/humancheck_captcha/"
                        "?next=%2Frecipe%2F104100931%2F"
                    )
                },
            )
        if "humancheck_captcha" in url:
            return httpx.Response(200, text="<html><body>captcha</body></html>")
        if url == "https://m.xiachufang.com/recipe/104100931/":
            return httpx.Response(200, text=recipe_html)
        return httpx.Response(404)

    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            transport=transport, follow_redirects=True
        ) as client:
            crawler = XiaChuFangCrawler(get_settings(), client=client, delay=0)
            return await crawler.parse_page(
                "https://www.xiachufang.com/recipe/104100931/"
            )

    recipe = asyncio.run(go())
    assert recipe.title == "牛油果生椰抹茶"
    assert recipe.source_url == "https://www.xiachufang.com/recipe/104100931/"
