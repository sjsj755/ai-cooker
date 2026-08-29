"""CLI 端到端（httpx MockTransport）：离线验证落盘/重跑/dry-run/失败路径。"""

import asyncio
from pathlib import Path

import httpx

from app.config import Settings
from app.crawlers.xiachufang import parse_explore_index
from scripts.crawl_recipes import main, run

FIXTURES = Path(__file__).parent / "fixtures"
ROBOTS = "User-agent: *\nCrawl-delay: 10\nDisallow: /search/\n"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8", errors="replace")


def _transport(fail_url: str | None = None) -> httpx.MockTransport:
    recipe_html = _fixture("xiachufang_recipe.html")
    index_html = _fixture("xiachufang_index.html")

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://www.xiachufang.com/robots.txt":
            return httpx.Response(200, text=ROBOTS)
        if url == "https://www.xiachufang.com/":
            return httpx.Response(200, text="<html><body>首页</body></html>")
        if url.startswith("https://www.xiachufang.com/explore/"):
            return httpx.Response(200, text=index_html)
        if url.startswith("https://www.xiachufang.com/recipe/"):
            if fail_url and url == fail_url:
                return httpx.Response(500, text="boom")
            return httpx.Response(200, text=recipe_html)
        return httpx.Response(404, text="not found")

    return httpx.MockTransport(handler)


def _settings() -> Settings:
    return Settings(crawler_retry=1, crawler_timeout_seconds=5)


def _run(tmp_path, **overrides) -> int:
    params = dict(
        site="xiachufang",
        source="explore",
        limit=2,
        dry_run=False,
        resume=True,
        force=False,
        delay=0,
        out_dir=tmp_path,
        transport=_transport(),
    )
    params.update(overrides)
    return asyncio.run(run(_settings(), **params))


def test_parse_limit(tmp_path):
    assert _run(tmp_path) == 0
    files = _recipe_files(tmp_path)
    assert len(files) == 2
    assert (tmp_path / "xiachufang" / "state.json").exists()


def test_resume_rerun_zero_new(tmp_path):
    assert _run(tmp_path, limit=25) == 0
    assert len(_recipe_files(tmp_path)) == 25
    assert _run(tmp_path, limit=25) == 0
    assert len(_recipe_files(tmp_path)) == 25


def test_dry_run_no_side_effects(tmp_path):
    assert _run(tmp_path, dry_run=True) == 0
    assert list((tmp_path / "xiachufang").glob("*.json")) == []


def test_failed_recorded(tmp_path):
    urls, _ = parse_explore_index(_fixture("xiachufang_index.html"))
    assert _run(tmp_path, limit=3, transport=_transport(fail_url=urls[0])) == 1
    failed = (tmp_path / "xiachufang" / "failed.jsonl").read_text(encoding="utf-8")
    assert urls[0] in failed
    assert "parse" in failed


def test_ingest_dry_run_without_key(tmp_path):
    assert main(["--stage", "ingest", "--dry-run", "--out-dir", str(tmp_path)]) == 0


def test_ingest_without_key_exit3(tmp_path):
    assert main(["--stage", "ingest", "--out-dir", str(tmp_path)]) == 3


def _recipe_files(tmp_path) -> list[Path]:
    return [
        p
        for p in (tmp_path / "xiachufang").glob("*.json")
        if p.name != "state.json"
    ]
