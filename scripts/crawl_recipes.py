"""CLI：下厨房采集（--stage parse）与入库（--stage ingest）。

用法示例：
    uv run python scripts/crawl_recipes.py --site xiachufang --stage parse --limit 5
    uv run python scripts/crawl_recipes.py --site xiachufang --stage parse --dry-run
    uv run python scripts/crawl_recipes.py --site xiachufang --stage parse --source category --limit 3
    uv run python scripts/crawl_recipes.py --site xiachufang --stage ingest
    uv run python scripts/crawl_recipes.py --site xiachufang --stage ingest --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import logging
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from app.config import Settings, get_settings
from app.core.fallback import FallbackError
from app.core.logging import get_logger, log_event, setup_logging
from app.core.openai_embeddings import EmbeddingConfigError
from app.crawlers.registry import registry
from app.crawlers.robots import fetch_robots
from app.crawlers.xiachufang import (
    AntiBotBlocked,
    CATEGORY_IDS,
    EXPLORE_URL,
    RobotsBlocked,
    XiaChuFangCrawler,
    parse_sitemap,
)
from app.ingestion.json_store import JsonStore, build_envelope
from app.ingestion.pipeline import run_ingest

logger = get_logger("crawl.cli")
SITEMAP_INDEX_URL = "https://www.xiachufang.com/sitemap.xml"


async def _sitemap_recipe_urls(crawler: XiaChuFangCrawler) -> list[str]:
    xml = await crawler.fetch_html(SITEMAP_INDEX_URL)
    urls: list[str] = []
    for loc in parse_sitemap(xml):
        if loc.endswith(".gz"):
            data = await crawler.fetch_bytes(loc)
            try:
                text = gzip.decompress(data).decode("utf-8", errors="replace")
            except OSError:
                text = data.decode("utf-8", errors="replace")
            urls.extend(parse_sitemap(text))
        else:
            urls.append(loc)
    return urls


async def run(
    settings: Settings,
    *,
    site: str = "xiachufang",
    source: str = "explore",
    limit: int | None = None,
    dry_run: bool = False,
    resume: bool = True,
    force: bool = False,
    delay: float | None = None,
    out_dir: str | Path | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> int:
    """执行一轮 parse 采集；返回退出码（0 成功 / 1 部分失败）。"""
    out_dir = Path(out_dir or settings.crawler_output_dir)
    delay = settings.crawler_delay_seconds if delay is None else delay

    async with httpx.AsyncClient(
        transport=transport,
        headers={"User-Agent": settings.crawler_ua},
        timeout=settings.crawler_timeout_seconds,
        follow_redirects=True,
    ) as client:
        try:
            robots = await fetch_robots(client)
        except httpx.HTTPError:
            robots = await fetch_robots(client, "https://m.xiachufang.com/robots.txt")
            log_event(
                logger,
                logging.WARNING,
                "crawl.cli.robots_fallback",
                url="https://m.xiachufang.com/robots.txt",
            )
        crawler = XiaChuFangCrawler(settings, client=client, robots=robots, delay=delay)
        registry.register(crawler)
        # 先访问首页建立会话 cookie，避免详情页被 302 到人机验证页
        warmup_url = (
            "https://m.xiachufang.com/" if source == "category" else "https://www.xiachufang.com/"
        )
        await crawler.fetch_html(warmup_url)
        log_event(logger, logging.INFO, "crawl.xiachufang.parse.warmup", url=warmup_url)
        store = JsonStore(out_dir)
        state = store.load_state(site)
        stats = {"saved": 0, "skipped": 0, "failed": 0, "seen": 0}
        consecutive_captcha = 0

        log_event(
            logger,
            logging.INFO,
            "crawl.xiachufang.parse.started",
            source=source,
            limit=limit,
            dry_run=dry_run,
            resume=resume,
            force=force,
            out_dir=str(out_dir),
            delay=delay,
        )

        async def process_url(url: str, discovered_from: str) -> str:
            nonlocal consecutive_captcha
            stats["seen"] += 1
            if store.exists(site, url) and resume and not force:
                stats["skipped"] += 1
                log_event(logger, logging.INFO, "crawl.xiachufang.parse.skipped", url=url)
                return "skipped"
            try:
                recipe = await crawler.parse_page(url)
            except AntiBotBlocked:
                consecutive_captcha += 1
                if consecutive_captcha >= 5:
                    raise
                stats["failed"] += 1
                error = "人机验证/反爬拦截（PC 与移动端均被拦）"
                record = {
                    "ts": _now(),
                    "url": url,
                    "stage": "parse",
                    "error": error,
                    "retries": settings.crawler_retry,
                }
                if not dry_run:
                    store.append_failed(site, record)
                log_event(
                    logger,
                    logging.WARNING,
                    "crawl.xiachufang.parse.anti_bot",
                    url=url,
                    error=error,
                )
                return "failed"
            except Exception as exc:  # noqa: BLE001 - 单条失败不中断整批
                consecutive_captcha = 0
                stats["failed"] += 1
                error = f"{type(exc).__name__}: {exc}"
                record = {
                    "ts": _now(),
                    "url": url,
                    "stage": "parse",
                    "error": error,
                    "retries": settings.crawler_retry,
                }
                if not dry_run:
                    store.append_failed(site, record)
                log_event(
                    logger,
                    logging.ERROR,
                    "crawl.xiachufang.parse.failed",
                    url=url,
                    error=error,
                )
                return "failed"
            consecutive_captcha = 0
            envelope = build_envelope(recipe, site, discovered_from=discovered_from)
            if dry_run:
                log_event(
                    logger,
                    logging.INFO,
                    "crawl.xiachufang.parse.dry_run",
                    url=url,
                    title=recipe.title,
                    ingredients=len(recipe.ingredients),
                    seasonings=len(recipe.seasonings),
                )
            else:
                store.write_recipe(site, url, envelope)
            stats["saved"] += 1
            log_event(
                logger,
                logging.INFO,
                "crawl.xiachufang.parse.saved",
                url=url,
                title=recipe.title,
            )
            return "saved"

        if source == "explore":
            sources_state = state.setdefault("sources", {})
            page = int(sources_state.get("explore", {}).get("next_page", 1))
            while True:
                urls, has_next = await crawler.fetch_index_page("explore", page)
                log_event(
                    logger,
                    logging.INFO,
                    "crawl.xiachufang.parse.index_page",
                    source="explore",
                    page=page,
                    urls=len(urls),
                    has_next=has_next,
                )
                unseen = 0
                early_stop = False
                for url in urls:
                    already = store.exists(site, url) and resume and not force
                    if not already:
                        unseen += 1
                    if limit is not None and stats["saved"] >= limit:
                        early_stop = True
                        break
                    if not already:
                        await process_url(
                            url,
                            discovered_from=(
                                EXPLORE_URL if page <= 1 else f"{EXPLORE_URL}?page={page}"
                            ),
                        )
                # 页面未消费完（因 limit 提前停）或已是最后一页时，
                # next_page 保持当前页，避免重跑跳过本页剩余菜谱。
                sources_state["explore"] = {
                    "pages_done": page,
                    "next_page": page if (early_stop or not has_next) else page + 1,
                    "last_run": _now(),
                }
                if not dry_run:
                    store.save_state(site, state)
                if limit is not None and stats["saved"] >= limit:
                    break
                if not has_next or unseen == 0:
                    break
                page += 1

        elif source == "category":
            for cat_id, cat_name in CATEGORY_IDS:
                if limit is not None and stats["saved"] >= limit:
                    break
                urls, _ = await crawler.fetch_index_page("category", cat_id=cat_id)
                log_event(
                    logger,
                    logging.INFO,
                    "crawl.xiachufang.parse.index_page",
                    source="category",
                    cat_id=cat_id,
                    cat_name=cat_name,
                    urls=len(urls),
                )
                for url in urls:
                    if limit is not None and stats["saved"] >= limit:
                        break
                    await process_url(
                        url,
                        discovered_from=f"https://m.xiachufang.com/category/{cat_id}/",
                    )

        elif source == "sitemap":
            urls = await _sitemap_recipe_urls(crawler)
            log_event(
                logger,
                logging.INFO,
                "crawl.xiachufang.parse.sitemap",
                urls=len(urls),
            )
            for url in urls:
                if limit is not None and stats["saved"] >= limit:
                    break
                await process_url(url, discovered_from=SITEMAP_INDEX_URL)

        else:
            raise ValueError(f"未知索引源: {source}")

        summary = {
            "source": source,
            "saved": stats["saved"],
            "skipped": stats["skipped"],
            "failed": stats["failed"],
            "seen": stats["seen"],
        }
        log_event(logger, logging.INFO, "crawl.xiachufang.parse.done", **summary)
        return 1 if stats["failed"] else 0


def _now() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat(timespec="seconds")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI 厨师菜谱采集 CLI（parse）")
    parser.add_argument("--site", default="xiachufang", help="适配器站点名")
    parser.add_argument("--stage", default="parse", choices=["parse", "ingest"])
    parser.add_argument(
        "--source",
        choices=["explore", "category", "sitemap"],
        default="explore",
        help="索引源",
    )
    parser.add_argument("--limit", type=int, default=None, help="本批最多落盘 N 条新菜谱")
    parser.add_argument("--dry-run", action="store_true", help="抓取+解析但不落盘")
    parser.add_argument("--resume", action="store_true", default=True, help="跳过已落盘（默认开）")
    parser.add_argument("--no-resume", action="store_false", dest="resume")
    parser.add_argument("--force", action="store_true", help="覆盖重爬已落盘条目")
    parser.add_argument("--delay", type=float, default=None, help="请求间隔秒")
    parser.add_argument("--out-dir", default=None, help="落盘根目录（默认 data/crawled）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    setup_logging(settings.log_level)

    if args.site != "xiachufang":
        print(f"未知站点: {args.site}", file=sys.stderr)
        return 2
    if args.delay is not None and args.delay < 10:
        log_event(
            logger,
            logging.WARNING,
            "crawl.cli.delay_below_robots",
            delay=args.delay,
            robots_crawl_delay=10,
        )
    if args.stage == "parse":
        try:
            return asyncio.run(
                run(
                    settings,
                    site=args.site,
                    source=args.source,
                    limit=args.limit,
                    dry_run=args.dry_run,
                    resume=args.resume,
                    force=args.force,
                    delay=args.delay,
                    out_dir=args.out_dir,
                )
            )
        except RobotsBlocked as exc:
            log_event(logger, logging.ERROR, "crawl.cli.blocked", error=str(exc))
            return 3
        except AntiBotBlocked as exc:
            log_event(logger, logging.ERROR, "crawl.cli.anti_bot", error=str(exc))
            return 3
        except (httpx.HTTPError, OSError) as exc:
            log_event(
                logger,
                logging.ERROR,
                "crawl.cli.network_error",
                error=f"{type(exc).__name__}: {exc}",
            )
            return 3
        except FallbackError as exc:
            log_event(
                logger,
                logging.ERROR,
                "crawl.cli.network_error",
                error=f"{type(exc).__name__}: {exc}",
            )
            return 3

    # ingest
    try:
        return asyncio.run(
            run_ingest(
                settings,
                site=args.site,
                limit=args.limit,
                dry_run=args.dry_run,
                force=args.force,
                out_dir=args.out_dir,
            )
        )
    except EmbeddingConfigError as exc:
        log_event(
            logger,
            logging.ERROR,
            "crawl.cli.embedding_config",
            error=str(exc),
        )
        return 3
    except (OSError, FallbackError) as exc:
        log_event(
            logger,
            logging.ERROR,
            "crawl.cli.ingest_infra_error",
            error=f"{type(exc).__name__}: {exc}",
        )
        return 3


if __name__ == "__main__":
    sys.exit(main())
