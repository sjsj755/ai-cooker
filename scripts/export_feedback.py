"""反馈导出 / 指标 / 归档（P5）：JSONL 导出 + like/dislike 分布 + 按菜谱聚合。

兼作 FEEDBACK_SALT 轮换的归档工具（默认方案：导出归档 → 清空 → 换盐 → 重启）。

用法：
    uv run python scripts/export_feedback.py --out data/feedback_archive.jsonl
    uv run python scripts/export_feedback.py --out data/feedback_archive.jsonl --truncate
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models import UserFeedback  # noqa: E402


def export_feedback(out: Path, truncate: bool = False) -> dict:
    """导出全部反馈为 JSONL（首行 _meta 含时间戳 / 条数 / 分布），可选清空。"""
    with SessionLocal() as session:
        rows = session.scalars(
            select(UserFeedback).order_by(UserFeedback.id)
        ).all()
        like = sum(1 for r in rows if r.action == "like")
        dislike = sum(1 for r in rows if r.action == "dislike")
        by_recipe: dict[int, dict[str, int]] = {}
        for r in rows:
            bucket = by_recipe.setdefault(r.recipe_id, {"like": 0, "dislike": 0})
            bucket[r.action] = bucket.get(r.action, 0) + 1

        meta = {
            "_meta": {
                "exported_at": datetime.now().astimezone().isoformat(
                    timespec="seconds"
                ),
                "count": len(rows),
                "like": like,
                "dislike": dislike,
                "recipes": len(by_recipe),
            }
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(meta, ensure_ascii=False) + "\n")
            for r in rows:
                fh.write(
                    json.dumps(
                        {
                            "id": r.id,
                            "recipe_id": r.recipe_id,
                            "action": r.action,
                            "created_at": r.created_at.isoformat()
                            if r.created_at
                            else None,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

        print(
            f"[export] {out} 条数={len(rows)} like={like} dislike={dislike} "
            f"按菜谱聚合={len(by_recipe)}"
        )
        top = sorted(by_recipe.items(), key=lambda kv: -sum(kv[1].values()))[:5]
        for recipe_id, counts in top:
            print(f"  recipe {recipe_id}: {counts}")

        if truncate:
            session.execute(delete(UserFeedback))
            session.commit()
            print(f"[export] 已清空反馈表（共 {len(rows)} 条）")
        return meta["_meta"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出反馈 / 指标 / 归档")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/feedback_archive.jsonl"),
        help="JSONL 输出路径（默认 data/feedback_archive.jsonl）",
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="导出后清空反馈表（FEEDBACK_SALT 轮换归档方案）",
    )
    args = parser.parse_args(argv)
    settings = get_settings()
    setup_logging(settings.log_level)
    export_feedback(args.out, truncate=args.truncate)
    return 0


if __name__ == "__main__":
    sys.exit(main())
