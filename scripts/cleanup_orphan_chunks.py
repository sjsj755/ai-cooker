"""清理孤儿块：Chroma recipe_docs 的 source_url 与 MySQL 求差集删除。

用法：
    uv run python scripts/cleanup_orphan_chunks.py --dry-run
    uv run python scripts/cleanup_orphan_chunks.py
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
from app.core.logging import get_logger, log_event, setup_logging  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models import Recipe  # noqa: E402
from app.vector_store import ChromaStore  # noqa: E402

logger = get_logger("cleanup.orphan")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="清理 Chroma 孤儿块")
    parser.add_argument("--dry-run", action="store_true", help="仅列出不删除")
    args = parser.parse_args(argv)
    settings = get_settings()
    setup_logging(settings.log_level)

    store = ChromaStore(settings)
    metas = asyncio.run(store.get_chunk_metadata(None))
    chroma_urls = sorted(
        {m.get("source_url") for m in metas if m.get("source_url")}
    )
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
