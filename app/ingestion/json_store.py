"""JSON 落盘：判重、读写、断点状态、失败清单。"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from app.core.crawler import CrawledRecipe

SCHEMA_VERSION = 1


def source_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def build_envelope(
    recipe: CrawledRecipe,
    site: str,
    discovered_from: str | None = None,
    crawled_at: str | None = None,
) -> dict:
    """封装 schema_version=1 的落盘信封。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "site": site,
        "crawled_at": crawled_at
        or datetime.now().astimezone().isoformat(timespec="seconds"),
        "discovered_from": discovered_from,
        "recipe": recipe.model_dump(mode="json"),
    }


def validate_envelope(data: object) -> tuple[bool, str]:
    """严格校验信封；返回 (是否有效, 原因)。"""
    if not isinstance(data, dict):
        return False, "不是 JSON 对象"
    if data.get("schema_version") != SCHEMA_VERSION:
        return False, f"schema_version={data.get('schema_version')!r}，预期 {SCHEMA_VERSION}"
    recipe = data.get("recipe")
    if not isinstance(recipe, dict):
        return False, "缺少 recipe 对象"
    try:
        CrawledRecipe.model_validate(recipe)
    except ValidationError as exc:
        return False, str(exc).splitlines()[0]
    return True, ""


class JsonStore:
    """data/crawled/{site}/ 目录读写：原子落盘、判重、断点状态、失败清单。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def site_dir(self, site: str) -> Path:
        path = self.root / site
        path.mkdir(parents=True, exist_ok=True)
        return path

    def path_for(self, site: str, url: str) -> Path:
        return self.site_dir(site) / f"{source_hash(url)}.json"

    def exists(self, site: str, url: str) -> bool:
        return self.path_for(site, url).exists()

    def read_recipe(self, site: str, url: str) -> dict | None:
        path = self.path_for(site, url)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def write_recipe(self, site: str, url: str, envelope: dict) -> Path:
        path = self.path_for(site, url)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(envelope, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)
        return path

    def append_failed(self, site: str, record: dict) -> Path:
        path = self.site_dir(site) / "failed.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path

    def load_state(self, site: str) -> dict:
        path = self.site_dir(site) / "state.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def save_state(self, site: str, state: dict) -> Path:
        path = self.site_dir(site) / "state.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)
        return path
