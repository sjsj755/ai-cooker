"""真实 LLM 输出采集（P5）：配 LLM_API_KEY 时重跑 parse / generate 输出落盘，
并更新 fixture_metadata.toml；无 key 跳过并提示（不报错）。

用法：
    uv run python scripts/capture_llm_fixtures.py                 # 无 key → 跳过
    LLM_API_KEY=... uv run python scripts/capture_llm_fixtures.py # 采集真实输出

升级/切换模型后必须重跑本脚本并人工复核结构字段（见 tests/fixtures/llm_responses/README.md）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.core.openai_llm import OpenAICompatibleLLM  # noqa: E402
from app.core.retriever import RecipeCandidate  # noqa: E402
from app.graph.nodes import get_llm_provider  # noqa: E402
from app.graph.prompts import generate_prompt, parse_prompt  # noqa: E402
from app.schemas.recommend import (  # noqa: E402
    IngredientExtractionList,
    RecommendationSet,
)

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "tests/fixtures/llm_responses"

PARSE_INPUTS = [
    "两个土豆、三颗鸡蛋",
    "家里有马铃薯和番茄",
    "洋葱一个，胡萝卜半根",
    "一斤牛肉，半斤虾仁",
]

GENERATE_CANDIDATES = [
    RecipeCandidate(
        recipe_id=1,
        title="家常土豆炒鸡蛋",
        match_score=0.012,
        missing_ingredients=[],
        difficulty=1,
        cook_time_minutes=20,
    ),
    RecipeCandidate(
        recipe_id=2,
        title="香辣牛肉炖土豆",
        match_score=0.008,
        missing_ingredients=["辣椒"],
        difficulty=2,
        cook_time_minutes=40,
    ),
]


def _load_toml(path: Path) -> dict:
    import tomllib

    with path.open("rb") as fh:
        return tomllib.load(fh)


def _write_toml(path: Path, data: dict) -> None:
    """按固定 key 顺序写回 toml（保留注释会丢失，属可接受代价；键必填字段保留）。"""
    lines = [
        "# LLM 输出 fixture 权威元数据（机器可读，tomllib 解析；README 仅人工说明）",
        "# 必填字段：schema_version / collection_date / model / capture.script",
        "",
        f"schema_version = {data['schema_version']}",
        f"collection_date = \"{data['collection_date']}\"",
        "",
        "[model]",
        f"provider = \"{data['model']['provider']}\"",
        f"name = \"{data['model']['name']}\"",
        "",
        "[capture]",
        f"script = \"{data['capture']['script']}\"",
        f"params = \"{data['capture']['params']}\"",
        "",
        "[desensitization]",
        f"note = \"{data['desensitization']['note']}\"",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_metadata(out_dir: Path, provider: str, model: str) -> None:
    toml_path = out_dir / "fixture_metadata.toml"
    meta = _load_toml(toml_path) if toml_path.exists() else {}
    meta.update(
        {
            "schema_version": 1,
            "collection_date": date.today().isoformat(),
            "model": {"provider": provider, "name": model},
            "capture": {
                "script": "scripts/capture_llm_fixtures.py",
                "params": f"parse_samples={len(PARSE_INPUTS)}; candidates={len(GENERATE_CANDIDATES)}",
            },
            "desensitization": {
                "note": "样例为合成 / 脱敏数据，不含真实用户个人信息；真实采集前请人工复核"
            },
        }
    )
    _write_toml(toml_path, meta)
    print(f"已更新 {toml_path.relative_to(Path.cwd())}")


async def _capture(provider, out_dir: Path) -> None:
    parse_out = {
        "inputs": PARSE_INPUTS,
        "outputs": [
            (await provider.structured(parse_prompt([raw]), IngredientExtractionList))
            .model_dump()
            for raw in PARSE_INPUTS
        ],
    }
    generate_out = {
        "inputs": {
            "candidates": [c.model_dump() for c in GENERATE_CANDIDATES],
        },
        "output": (
            await provider.structured(
                generate_prompt(
                    GENERATE_CANDIDATES,
                    ingredients=["土豆", "鸡蛋"],
                    exclude_tags=[],
                ),
                RecommendationSet,
            )
        ).model_dump(),
    }
    (out_dir / "parse_sample.json").write_text(
        json.dumps(parse_out, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "generate_sample.json").write_text(
        json.dumps(generate_out, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("已写入 parse_sample.json / generate_sample.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="采集真实 LLM 输出为 fixture")
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="fixture 输出目录（默认 tests/fixtures/llm_responses）",
    )
    args = parser.parse_args(argv)
    settings = get_settings()
    setup_logging(settings.log_level)
    args.out.mkdir(parents=True, exist_ok=True)

    provider = get_llm_provider()
    if provider is None:
        print("跳过：未配置 LLM_API_KEY（或 LLM_MOCK=true），无法采集真实 LLM 输出")
        return 0
    if settings.llm_mock:
        print("跳过：LLM_MOCK=true 时采集的是 mock 输出，非真实输出；请关闭后重跑")
        return 0
    assert isinstance(provider, OpenAICompatibleLLM)
    asyncio.run(_capture(provider, args.out))
    _update_metadata(
        args.out,
        provider="openai-compatible",
        model=settings.llm_model,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
