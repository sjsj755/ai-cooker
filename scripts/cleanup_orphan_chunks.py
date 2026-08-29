"""清理孤儿块：分页扫描 Chroma recipe_docs 的 source_url，与 MySQL 求差集删除。

用法：
    uv run python scripts/cleanup_orphan_chunks.py --dry-run [--max-retries 3]
    uv run python scripts/cleanup_orphan_chunks.py [--max-retries 3]

扫描与删除分离：先全量分页收集 source_url，再统一比对 MySQL 后删除；
单页读取重试后仍失败即终止（退出码 3），不基于部分扫描结果删除。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.core.fallback import FallbackError  # noqa: E402
from app.core.logging import get_logger, log_event, setup_logging  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models import Recipe  # noqa: E402
from app.vector_store import ChromaStore  # noqa: E402

logger = get_logger("cleanup.orphan")


async def _scan_urls(store: ChromaStore, max_retries: int) -> set[str]:
    """分页扫描全部块元数据，收集 source_url 集合（扫描与删除分离）。"""
    urls: set[str] = set()
    scanned = 0
    async for meta in store.iter_chunk_metadata(max_attempts=max_retries):
        scanned += 1
        url = meta.get("source_url")
        if url:
            urls.add(url)
        if scanned % 10_000 == 0:
            log_event(
                logger,
                logging.INFO,
                "cleanup.orphan.scan_progress",
                scanned=scanned,
                unique_urls=len(urls),
            )
    log_event(
        logger,
        logging.INFO,
        "cleanup.orphan.scan_done",
        scanned=scanned,
        unique_urls=len(urls),
    )
    return urls


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="清理 Chroma 孤儿块")
    parser.add_argument("--dry-run", action="store_true", help="仅列出不删除")
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="单页读取失败重试次数（默认 3）",
    )
    args = parser.parse_args(argv)
    if args.max_retries < 1:
        print("--max-retries 必须 >= 1", file=sys.stderr)
        return 2
    settings = get_settings()
    setup_logging(settings.log_level)

    store = ChromaStore(settings)
    try:
        chroma_urls = sorted(asyncio.run(_scan_urls(store, args.max_retries)))
    except FallbackError as exc:
        log_event(
            logger,
            logging.ERROR,
            "cleanup.orphan.scan_failed",
            error=str(exc),
            max_retries=args.max_retries,
        )
        print(
            f"扫描孤儿块失败（单页读取重试 {args.max_retries} 次后仍失败），未删除任何数据",
            file=sys.stderr,
        )
        return 3
    with SessionLocal() as session:
        db_urls = set(session.scalars(select(Recipe.source_url)).all())
    orphans = [url for url in chroma_urls if url not in db_urls]

    if not orphans:
        print("无孤儿块")
        return 0
    log_event(
        logger,
        logging.WARNING,
        "cleanup.orphan.found",
        count=len(orphans),
        dry_run=args.dry_run,
    )
    for url in orphans:
        print(f"[{'dry-run' if args.dry_run else 'delete'}] {url}")
    if args.dry_run:
        return 0
    for url in orphans:
        asyncio.run(store.delete_where({"source_url": url}))
    print(f"已删除 {len(orphans)} 个孤儿块")
    return 0


if __name__ == "__main__":
    sys.exit(main())
