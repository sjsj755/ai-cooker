"""P5 反馈导出：JSONL 可解析 / 时间戳与条数 / like-dislike 分布 / 按菜谱聚合 / 清空。"""

import json
from pathlib import Path

from sqlalchemy import delete, func, select

from app.models import UserFeedback
from scripts.export_feedback import export_feedback
from tests.helpers import add_recipe, delete_recipe

URL_A = "https://test.export/1"
URL_B = "https://test.export/2"


def _seed_feedback(db):
    # 清空反馈表，避免历史残留（recipe_id 置 NULL 的行不会随菜谱删除消失）干扰计数
    db.execute(delete(UserFeedback))
    db.commit()
    rid_a = add_recipe("导出菜谱A", URL_A, ingredients=["土豆"], tags=["家常菜"])
    rid_b = add_recipe("导出菜谱B", URL_B, ingredients=["鸡蛋"], tags=["家常菜"])
    rows = [
        UserFeedback(recipe_id=rid_a, client_fingerprint="a" * 64, action="like"),
        UserFeedback(recipe_id=rid_a, client_fingerprint="b" * 64, action="like"),
        UserFeedback(recipe_id=rid_a, client_fingerprint="c" * 64, action="dislike"),
        UserFeedback(recipe_id=rid_b, client_fingerprint="d" * 64, action="like"),
    ]
    db.add_all(rows)
    db.commit()
    return rid_a, rid_b


def _parse_jsonl(path: Path) -> tuple[dict, list[dict]]:
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert lines, "JSONL 为空"
    meta = json.loads(lines[0])
    rows = [json.loads(line) for line in lines[1:]]
    return meta, rows


def test_export_feedback_jsonl_metrics(tmp_path, db_session):
    rid_a, rid_b = _seed_feedback(db_session)
    try:
        out = tmp_path / "feedback_archive.jsonl"
        meta = export_feedback(out)
        assert meta["count"] == 4
        assert meta["like"] == 3
        assert meta["dislike"] == 1
        assert meta["recipes"] == 2
        assert meta["exported_at"]

        parsed_meta, rows = _parse_jsonl(out)
        assert parsed_meta["_meta"]["count"] == 4
        assert len(rows) == 4
        for row in rows:
            assert set(row) == {"id", "recipe_id", "action", "created_at"}
            assert row["action"] in {"like", "dislike"}
        by_recipe = {}
        for row in rows:
            by_recipe.setdefault(row["recipe_id"], []).append(row["action"])
        assert sorted(by_recipe[rid_a]) == ["dislike", "like", "like"]
        assert by_recipe[rid_b] == ["like"]
    finally:
        db_session.execute(
            delete(UserFeedback).where(UserFeedback.recipe_id.in_([rid_a, rid_b]))
        )
        db_session.commit()
        delete_recipe(URL_A)
        delete_recipe(URL_B)


def test_export_feedback_truncate_clears_table(tmp_path, db_session):
    rid_a, rid_b = _seed_feedback(db_session)
    try:
        out = tmp_path / "archive.jsonl"
        meta = export_feedback(out, truncate=True)
        assert meta["count"] == 4
        remaining = db_session.scalar(
            select(func.count()).select_from(UserFeedback)
        )
        assert remaining == 0
    finally:
        delete_recipe(URL_A)
        delete_recipe(URL_B)
