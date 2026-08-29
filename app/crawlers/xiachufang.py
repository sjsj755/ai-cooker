"""下厨房适配器：索引页 URL 发现 + 菜谱详情页解析（PC/移动）。"""

from __future__ import annotations

import asyncio
import json
import random
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.config import Settings
from app.core.crawler import CrawledIngredient, CrawledRecipe, RecipeCrawler
from app.core.fallback import FallbackError
from app.core.html_clean import clean_html, clean_multiline, clean_text
from app.core.seasoning_words import classify_seasoning
from app.crawlers.robots import RobotsRules

RECIPE_HREF_RE = re.compile(r"^/recipe/(\d+)/?$")
EXPLORE_URL = "https://www.xiachufang.com/explore/"
ROBOTS_URL = "https://www.xiachufang.com/robots.txt"
RATE_LIMIT_BACKOFF_SECONDS = 30.0

CATEGORY_IDS: list[tuple[str, str]] = [
    ("40076", "家常菜"),
    ("40077", "快手菜"),
    ("40078", "下饭菜"),
    ("40071", "早餐"),
    ("40073", "小吃"),
    ("51761", "烘焙"),
    ("20130", "汤羹"),
    ("30048", "减肥"),
]


class RobotsBlocked(Exception):
    """robots.txt 禁止访问该 URL。"""


class AntiBotBlocked(Exception):
    """命中站点人机验证/反爬页（如 /auth/humancheck_captcha/）。"""


class PageParseError(Exception):
    """页面无法解析为菜谱。"""


def _absolute(base_url: str, href: str) -> str:
    if href.startswith("http://") or href.startswith("https://"):
        return href
    parsed = urlparse(base_url)
    return f"{parsed.scheme}://{parsed.netloc}{href}"


def _normalize_url(url: str) -> str:
    """去掉 query/fragment，仅保留协议+域名+路径。"""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def _recipe_hrefs(soup: BeautifulSoup) -> list[str]:
    return [
        a.get("href", "")
        for a in soup.select('a[href^="/recipe/"]')
        if RECIPE_HREF_RE.fullmatch(a.get("href", ""))
    ]


def parse_explore_index(html: str) -> tuple[list[str], bool]:
    """PC explore 索引：返回 (菜谱 URL 列表, 是否有下一页)。"""
    soup = clean_html(html)
    urls = list(dict.fromkeys(_absolute(EXPLORE_URL, h) for h in _recipe_hrefs(soup)))
    pager = soup.select_one("div.pager")
    has_next = bool(pager and pager.select("a.next[href]"))
    return urls, has_next


def parse_mobile_category_index(html: str, base_url: str) -> list[str]:
    """移动分类页索引：返回菜谱 URL 列表（仅首页，不分页）。"""
    soup = clean_html(html)
    return list(dict.fromkeys(_absolute(base_url, h) for h in _recipe_hrefs(soup)))


def parse_sitemap(xml_text: str) -> list[str]:
    """解析 sitemap（支持 sitemapindex 与 urlset），返回全部 <loc>。"""
    urls: list[str] = []

    def collect(elem: ET.Element) -> None:
        tag = elem.tag.rsplit("}", 1)[-1]
        if tag == "loc" and elem.text:
            urls.append(elem.text.strip())
        for child in elem:
            collect(child)

    try:
        root = ET.fromstring(xml_text or "")
    except ET.ParseError:
        return []
    collect(root)
    return urls


def _split_instructions(text: str) -> list[str]:
    """JSON-LD 步骤兜底拆分：按逗号/编号标记切分（仅 fallback 用）。"""
    parts = re.split(r"[,，]\s*|(?<=\S)\s+(?=\d+[.、．])", text.strip())
    out: list[str] = []
    for part in parts:
        part = re.sub(r"^\s*\d+[.、．]\s*", "", part).strip()
        if part:
            out.append(part)
    return out


def _ldjson_recipe(soup: BeautifulSoup) -> dict | None:
    for tag in soup.select("script[type='application/ld+json']"):
        raw = tag.string or tag.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Recipe":
            return data
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("@type") == "Recipe":
                    return item
    return None


class XiaChuFangCrawler(RecipeCrawler):
    """下厨房适配器：PC/移动详情解析 + explore/分类/sitemap URL 发现。"""

    name = "xiachufang"

    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        robots: RobotsRules | None = None,
        delay: float | None = None,
    ) -> None:
        self.settings = settings
        self._client = client
        self._robots = robots
        self._delay = settings.crawler_delay_seconds if delay is None else delay
        self._allowed_domains = set(settings.crawler_allowed_domains)

    # ---------- 安全校验 ----------

    def _check_domain(self, url: str) -> None:
        host = urlparse(url).hostname or ""
        if host not in self._allowed_domains:
            raise ValueError(f"域名不在白名单: {host}（{url}）")

    def _check_robots(self, url: str) -> None:
        if self._robots is not None and not self._robots.allowed(url):
            raise RobotsBlocked(f"robots.txt 禁止: {url}")

    # ---------- HTTP（指数退避重试） ----------

    async def _fetch_once(self, url: str) -> httpx.Response:
        if self._client is None:
            raise RuntimeError("适配器未配置 HTTP 客户端")
        self._check_domain(url)
        self._check_robots(url)
        await asyncio.sleep(self._delay)
        resp = await self._client.get(url)
        resp.raise_for_status()
        final_url = str(resp.url)
        self._check_domain(final_url)
        if _is_anti_bot_path(urlparse(final_url).path):
            raise AntiBotBlocked(f"人机验证/反爬页: {final_url}")
        return resp

    async def _fetch_retried(self, url: str) -> httpx.Response:
        """指数退避重试；反爬命中不重试，直接向上抛。"""
        last_exc: Exception | None = None
        for attempt in range(1, self.settings.crawler_retry + 1):
            try:
                return await self._fetch_once(url)
            except AntiBotBlocked:
                raise
            except Exception as exc:  # noqa: BLE001 - 统一重试，超限抛 FallbackError
                last_exc = exc
                if attempt >= self.settings.crawler_retry:
                    break
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
                    await asyncio.sleep(RATE_LIMIT_BACKOFF_SECONDS)
                else:
                    delay = min(8.0, 0.5 * (2 ** (attempt - 1)))
                    await asyncio.sleep(delay + random.uniform(0, delay * 0.2))
        raise FallbackError(
            f"重试 {self.settings.crawler_retry} 次后仍失败: "
            f"{type(last_exc).__name__}: {last_exc}"
        ) from last_exc

    async def fetch_html(self, url: str) -> str:
        return (await self._fetch_retried(url)).text

    async def fetch_bytes(self, url: str) -> bytes:
        return (await self._fetch_retried(url)).content

    # ---------- 索引 ----------

    async def fetch_index_page(
        self,
        source: str,
        page: int = 1,
        cat_id: str | None = None,
    ) -> tuple[list[str], bool]:
        """抓取一页索引，返回 (URL 列表, 是否有下一页)。"""
        if source == "explore":
            url = EXPLORE_URL if page <= 1 else f"{EXPLORE_URL}?page={page}"
            return parse_explore_index(await self.fetch_html(url))
        if source == "category":
            if cat_id is None:
                raise ValueError("category 源需要 cat_id")
            url = f"https://m.xiachufang.com/category/{cat_id}/"
            return parse_mobile_category_index(await self.fetch_html(url), base_url=url), False
        raise ValueError(f"未知索引源: {source}")

    async def fetch_index(
        self,
        source: str = "explore",
        page: int = 1,
        cat_id: str | None = None,
    ) -> list[str]:
        urls, _ = await self.fetch_index_page(source=source, page=page, cat_id=cat_id)
        return urls

    # ---------- 详情解析 ----------

    async def parse_page(
        self,
        url: str,
        html: str | None = None,
        source_url: str | None = None,
    ) -> CrawledRecipe:
        """抓取并解析详情页；PC 被反爬拦截时自动回退移动端同 URL。"""
        if html is not None:
            return self.parse_recipe_html(url, html, source_url=source_url)
        try:
            html = await self.fetch_html(url)
        except AntiBotBlocked:
            if urlparse(url).hostname == "www.xiachufang.com":
                m_url = _normalize_url(url).replace(
                    "https://www.xiachufang.com", "https://m.xiachufang.com"
                )
                html = await self.fetch_html(m_url)
                return self.parse_recipe_html(
                    m_url,
                    html,
                    source_url=source_url or _normalize_url(url),
                )
            raise
        return self.parse_recipe_html(url, html, source_url=source_url)

    def parse_recipe_html(
        self,
        url: str,
        html: str,
        source_url: str | None = None,
    ) -> CrawledRecipe:
        # JSON-LD 需在清洗（会移除 script）之前提取
        ld = _ldjson_recipe(BeautifulSoup(html or "", "html.parser"))
        soup = clean_html(html)
        mobile = (
            urlparse(url).hostname == "m.xiachufang.com"
            or soup.select_one("h1.recipe-name") is not None
        )
        if mobile:
            title, desc, items, steps, tags = self._extract_mobile(soup, ld)
        else:
            title, desc, items, steps, tags = self._extract_desktop(soup, ld)
        if not title:
            raise PageParseError("页面缺少标题，疑似反爬/非菜谱页")
        ingredients, seasonings = self._split(items)
        return CrawledRecipe(
            title=title,
            source_url=source_url or self._canonical_source_url(soup, url),
            description=desc or None,
            ingredients=ingredients,
            seasonings=seasonings,
            tags=tags,
            steps=[{"instruction": s, "minutes": None} for s in steps],
        )

    def _extract_desktop(
        self,
        soup: BeautifulSoup,
        ld: dict | None,
    ) -> tuple[str | None, str | None, list[CrawledIngredient], list[str], list[str]]:
        title_el = soup.select_one("h1.page-title")
        title = clean_text(title_el.get_text()) if title_el else None
        if not title and ld:
            title = clean_text(ld.get("name"))

        desc_el = soup.select_one("div.desc.mt30") or soup.select_one("div.desc")
        desc = clean_text(desc_el.get_text()) if desc_el else None
        if not desc and ld:
            desc = clean_text(ld.get("description"))

        items: list[CrawledIngredient] = []
        for tr in soup.select("div.ings table tr"):
            name_el = tr.select_one("td.name a") or tr.select_one("td.name")
            if name_el is None:
                continue
            name = clean_text(name_el.get_text())
            if not name:
                continue
            unit_el = tr.select_one("td.unit")
            items.append(
                CrawledIngredient(
                    name=name,
                    amount=(clean_text(unit_el.get_text()) or None) if unit_el else None,
                )
            )

        steps = [
            clean_multiline(p.get_text())
            for p in soup.select("div.steps ol li p.text")
        ]
        if not steps and ld and ld.get("recipeInstructions"):
            steps = _split_instructions(str(ld["recipeInstructions"]))

        tags = [
            clean_text(a.get_text())
            for a in soup.select(".recipe-tags .recipe-cats a[href^='/category/']")
        ]
        if not tags and ld and ld.get("recipeCategory"):
            tags = [clean_text(ld["recipeCategory"])]
        return title, desc, items, steps, tags

    def _extract_mobile(
        self,
        soup: BeautifulSoup,
        ld: dict | None,
    ) -> tuple[str | None, str | None, list[CrawledIngredient], list[str], list[str]]:
        title_el = soup.select_one("h1.recipe-name")
        title = clean_text(title_el.get_text()) if title_el else None
        if not title and ld:
            title = clean_text(ld.get("name"))

        desc_el = soup.select_one("section.recipe-desc")
        desc = clean_text(desc_el.get_text()) if desc_el else None
        if not desc and ld:
            desc = clean_text(ld.get("description"))

        items: list[CrawledIngredient] = []
        for line in soup.select("section#ings .recipe-ingredient a.ing-line"):
            name_el = line.select_one(".ing-name")
            if name_el is None:
                continue
            name = clean_text(name_el.get_text())
            if not name:
                continue
            amount_el = line.select_one(".ing-amount")
            items.append(
                CrawledIngredient(
                    name=name,
                    amount=(clean_text(amount_el.get_text()) or None)
                    if amount_el
                    else None,
                )
            )

        steps = [
            clean_text(p.get_text())
            for p in soup.select("section#steps .recipe-steps .step .step-text")
        ]
        if not steps and ld and ld.get("recipeInstructions"):
            steps = _split_instructions(str(ld["recipeInstructions"]))

        tags: list[str] = []
        if ld and ld.get("recipeCategory"):
            tags = [clean_text(ld["recipeCategory"])]
        return title, desc, items, steps, tags

    def _canonical_source_url(self, soup: BeautifulSoup, fallback_url: str) -> str:
        link = soup.select_one("link[rel='canonical']")
        candidates = [link.get("href") if link else None, fallback_url]
        for candidate in candidates:
            if not candidate:
                continue
            host = urlparse(candidate).hostname or ""
            if host in self._allowed_domains:
                return _normalize_url(candidate)
        raise PageParseError(f"无法得到白名单内 canonical URL: {fallback_url}")

    @staticmethod
    def _split(
        items: list[CrawledIngredient],
    ) -> tuple[list[CrawledIngredient], list[CrawledIngredient]]:
        ingredients: list[CrawledIngredient] = []
        seasonings: dict[str, list[str]] = {}
        for item in items:
            canonical = classify_seasoning(item.name)
            if canonical:
                seasonings.setdefault(canonical, []).append(item.amount or "")
            else:
                ingredients.append(item)
        seasoning_items = [
            CrawledIngredient(
                name=name,
                amount="、".join(a for a in amounts if a) or None,
                is_essential=False,
            )
            for name, amounts in seasonings.items()
        ]
        return ingredients, seasoning_items


def _is_anti_bot_path(path: str) -> bool:
    return "/auth/" in path or "captcha" in path.lower()
