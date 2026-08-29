"""识别质量评测：10 条用例项级准确率基线（口语化/别名/量词/无标点/无效输入）。

用法：
    uv run python scripts/eval_recommend.py                 # 真实 LLM 评测（需 LLM_API_KEY）
    uv run python scripts/eval_recommend.py --min-accuracy 0.85

评估口径：parse（LLM 识别）+ link（四级映射）后，期望食材命中
normalized_name（未映射用 raw_name）即为正确；项级准确率 = 命中数 / 期望总数。
无效输入用例（期望为空）单独校验“无虚构”，不计入准确率分母。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.core.langsmith_trace import maybe_trace  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.graph.nodes import get_llm_provider, link_node, parse_node  # noqa: E402
from app.graph.state import CookState, empty_state  # noqa: E402

# (用户输入, 期望食材；每项为同义候选元组，命中任一即正确)
EVAL_CASES: list[tuple[str, list[tuple[str, ...]]]] = [
    ("土豆 鸡蛋", [("土豆",), ("鸡蛋",)]),
    ("两个土豆、三颗鸡蛋", [("土豆",), ("鸡蛋",)]),
    ("家里有马铃薯和番茄", [("土豆",), ("西红柿",)]),
    ("洋芋青椒", [("土豆",), ("青椒",)]),
    ("我想做红烧肉，有五花肉", [("猪肉",)]),
    ("冰箱里有：黄瓜、豆腐、大白菜", [("黄瓜",), ("豆腐",), ("白菜",)]),
    ("洋葱一个，胡萝卜半根", [("洋葱",), ("胡萝卜",)]),
    ("一斤牛肉，半斤虾仁", [("牛肉",), ("虾", "虾仁")]),
    ("鸡腿肉、猪五花", [("鸡肉",), ("猪肉",)]),
    ("今天什么都不想做", []),
]


async def run_case(raw: str) -> tuple[list[str], str]:
    """parse → link，返回识别出的标准名列表（未映射用 raw_name）。"""
    parsed = await parse_node(empty_state(ingredients=[raw]))
    if parsed["parse_error"]:
        return [], "parse_failed"
    linked = await link_node(CookState(**parsed))
    items = linked["parsed_ingredients"]
    return [p.normalized_name or p.raw_name for p in items], "ok"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="推荐识别质量评测（10 条基线）")
    parser.add_argument(
        "--min-accuracy", type=float, default=0.85, help="项级准确率门禁（默认 0.85）"
    )
    parser.add_argument("--trace", action="store_true", help="上传 runs 到 LangSmith（无 key 跳过）")
    args = parser.parse_args(argv)
    settings = get_settings()
    setup_logging(settings.log_level)

    if get_llm_provider() is None:
        print("跳过：未配置 LLM_API_KEY，无法运行识别评测")
        return 0

    run_case = maybe_trace(run_case, "eval_recommend", args.trace)
    total = hits = 0
    print(f"识别评测（{len(EVAL_CASES)} 条用例）")
    for raw, expected in EVAL_CASES:
        names, status = asyncio.run(run_case(raw))
        if not expected:
            invented = bool(names)
            print(
                f"  [{status}] {raw!r} → 无食材期望，识别到 {len(names)} 项"
                + ("（虚构）" if invented else "")
            )
            continue
        matched = [
            e for e in expected if any(alt in names for alt in e)
        ]
        hits += len(matched)
        total += len(expected)
        ok = len(matched) == len(expected)
        print(
            f"  [{status}] {raw!r} → {names} "
            f"期望 {expected} 命中 {len(matched)}/{len(expected)} "
            f"{'PASS' if ok else 'FAIL'}"
        )

    accuracy = hits / total if total else 1.0
    print(f"\n项级准确率 {hits}/{total} = {accuracy:.3f}（门禁 ≥ {args.min_accuracy}）")
    if accuracy < args.min_accuracy:
        print(f"FAIL：识别基线 {accuracy:.3f} < {args.min_accuracy}", file=sys.stderr)
        return 1
    print(f"PASS：识别基线 {accuracy:.3f} ≥ {args.min_accuracy}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
