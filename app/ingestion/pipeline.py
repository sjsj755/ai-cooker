"""ingest 管线：扫描 JSON → 校验 → MySQL 幂等入库 → 分块嵌入 → Chroma upsert。"""

from __future__ import annotations

import json
import logging
import re
import shutil
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.core.crawler import CrawledRecipe
from app.core.embeddings import EmbeddingProvider
from app.core.logging import get_logger, log_event
from app.core.openai_embeddings import OpenAICompatibleEmbeddings
from app.crawlers.xiachufang import XiaChuFangCrawler
from app.db.session import SessionLocal
from app.ingestion.json_store import JsonStore, source_hash, validate_envelope
from app.ingestion.text_builder import chunk_recipe
from app.models import Recipe
from app.vector_store import ChromaStore

logger = get_logger("crawl.cli")

_HASH_FILE_RE = re.compile(r"^[0-9a-f]{64}\.json$")
MAX_CONSECUTIVE_FAILURES = 5


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _scan_files(store: JsonStore, site: str) -> list[Path]:
    return sorted(
        path
        for path in store.site_dir(site).glob("*.json")
        if _HASH_FILE_RE.match(path.name)
    )


def _move_invalid(store: JsonStore, site: str, path: Path, reason: str) -> None:
    invalid_dir = store.site_dir(site) / "invalid"
    invalid_dir.mkdir(exist_ok=True)
    shutil.move(str(path), str(invalid_dir / path.name))
    with (invalid_dir / "reasons.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {"ts": _now(), "file": path.name, "reason": reason},
                ensure_ascii=False,
            )
            + "\n"
        )


async def run_ingest(
    settings: Settings,
    *,
    site: str = "xiachufang",
    limit: int | None = None,
    dry_run: bool = False,
    force: bool = False,
    out_dir: str | Path | None = None,
    embeddings: EmbeddingProvider | None = None,
    chroma: ChromaStore | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
) -> int:
    """执行一轮 ingest；返回退出码（0 成功 / 1 部分失败 / 3 基础设施熔断）。"""
    out_dir = Path(out_dir or settings.crawler_output_dir)
    store = JsonStore(out_dir)
    files = _scan_files(store, site)
    if limit is not None:
        files = files[:limit]
    stats = {"seen": 0, "saved": 0, "skipped": 0, "failed": 0, "invalid": 0}
    consecutive_failures = 0

    if not dry_run:
        embeddings = embeddings or OpenAICompatibleEmbeddings(settings)
        chroma = chroma or ChromaStore(settings)

    log_event(
        logger,
        logging.INFO,
        f"crawl.{site}.ingest.started",
        files=len(files),
        dry_run=dry_run,
        force=force,
        limit=limit,
        out_dir=str(out_dir),
    )

    for path in files:
        stats["seen"] += 1
        result = await _ingest_file(
            settings=settings,
            store=store,
            site=site,
            path=path,
            embeddings=embeddings,
            chroma=chroma,
            force=force,
            dry_run=dry_run,
            session_factory=session_factory,
        )
        if result == "saved":
            stats["saved"] += 1
            consecutive_failures = 0
        elif result == "skipped":
            stats["skipped"] += 1
            consecutive_failures = 0
        elif result == "invalid":
            stats["invalid"] += 1
            consecutive_failures = 0
        else:  # failed
            stats["failed"] += 1
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                log_event(
                    logger,
                    logging.ERROR,
                    f"crawl.{site}.ingest.circuit_breaker",
                    consecutive_failures=consecutive_failures,
                    error="连续失败达到阈值，疑似基础设施故障，中止本批",
                )
                return 3

    summary = {k: v for k, v in stats.items()}
    log_event(logger, logging.INFO, f"crawl.{site}.ingest.done", **summary)
    return 1 if stats["failed"] else 0


async def _ingest_file(
    *,
    settings: Settings,
    store: JsonStore,
    site: str,
    path: Path,
    embeddings: EmbeddingProvider | None,
    chroma: ChromaStore | None,
    force: bool,
    dry_run: bool,
    session_factory: Callable[[], Session],
) -> str:
    """处理单个 JSON；返回 saved/skipped/failed/invalid。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    ok, reason = validate_envelope(data)
    if not ok:
        if not dry_run:
            _move_invalid(store, site, path, reason)
        log_event(
            logger,
            logging.ERROR,
            f"crawl.{site}.ingest.invalid",
            file=path.name,
            reason=reason,
        )
        return "invalid"

    recipe = CrawledRecipe.model_validate(data["recipe"])
    url = recipe.source_url

    if dry_run:
        log_event(
            logger,
            logging.INFO,
            f"crawl.{site}.ingest.file_ok",
            file=path.name,
            url=url,
            title=recipe.title,
            dry_run=True,
        )
        return "saved"  # dry-run 统计为将入库

    # MySQL：命中跳过；--force 同一事务内删除重建
    try:
        crawler = XiaChuFangCrawler(settings)
        with session_factory() as session:
            existing = session.scalar(
                select(Recipe).where(Recipe.source_url == url)
            )
            if existing is not None and not force:
                status = "skipped"
            else:
                if existing is not None:
                    session.delete(existing)
                    session.flush()
                crawler.save(session, recipe)
                session.commit()
                status = "saved"
    except Exception as exc:  # noqa: BLE001 - 单条失败不中断整批
        _record_failed(store, site, url, path, exc)
        return "failed"

    # Chroma：即使 MySQL 跳过也 upsert（崩溃自愈）
    chunks = chunk_recipe(recipe)
    if not chunks:
        log_event(
            logger,
            logging.WARNING,
            f"crawl.{site}.ingest.empty_document",
            url=url,
            title=recipe.title,
        )
        _log_ok(site, path, url, recipe, chunks=0, status=status)
        return status
    try:
        vectors = await embeddings.embed_texts(  # type: ignore[union-attr]
            [chunk.text for chunk in chunks]
        )
        doc_hash = source_hash(url)
        ids = [f"{doc_hash}#{i}" for i in range(len(chunks))]
        metadatas = []
        for i, chunk in enumerate(chunks):
            meta = {
                "source_url": url,
                "title": recipe.title,
                "site": site,
                "chunk_index": i,
                "unit_type": chunk.unit_type,
            }
            if chunk.step_start is not None:
                meta["step_start"] = chunk.step_start
                meta["step_end"] = chunk.step_end
            metadatas.append(meta)
        # 嵌入成功后先清理旧块，再写新块，避免孤儿块残留
        await chroma.delete_where({"source_url": url})  # type: ignore[union-attr]
        await chroma.upsert(  # type: ignore[union-attr]
            ids=ids,
            documents=[chunk.text for chunk in chunks],
            metadatas=metadatas,
            embeddings=vectors,
        )
    except Exception as exc:  # noqa: BLE001
        error = (
            f"{type(exc).__name__}: {exc}（MySQL 可能已提交，重跑会自愈 Chroma）"
        )
        _record_failed(store, site, url, path, exc, note=error)
        return "failed"

    _log_ok(site, path, url, recipe, chunks=len(chunks), status=status)
    return status


def _log_ok(
    site: str,
    path: Path,
    url: str,
    recipe: CrawledRecipe,
    chunks: int,
    status: str,
) -> None:
    log_event(
        logger,
        logging.INFO,
        f"crawl.{site}.ingest.file_ok" if status == "saved" else f"crawl.{site}.ingest.file_skipped",
        file=path.name,
        url=url,
        title=recipe.title,
        chunks=chunks,
    )


def _record_failed(
    store: JsonStore,
    site: str,
    url: str,
    path: Path,
    exc: Exception,
    note: str | None = None,
) -> None:
    error = note or f"{type(exc).__name__}: {exc}"
    store.append_failed(
        site,
        {
            "ts": _now(),
            "url": url,
            "file": path.name,
            "stage": "ingest",
            "error": error,
            "retries": 3,
        },
    )
    log_event(
        logger,
        logging.ERROR,
        f"crawl.{site}.ingest.file_failed",
        file=path.name,
        url=url,
        error=error,
    )
