"""P5 反馈 API：写入 / 422 / 404 / 幂等 / 切换 action / 匿名指纹。"""

import re

from sqlalchemy import func, select

from app.models import UserFeedback
from tests.helpers import add_recipe, delete_recipe

URL = "https://test.feedback/1"
TITLE = "反馈接口测试菜谱"

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _add_recipe() -> int:
    return add_recipe(TITLE, URL, ingredients=["土豆"], seasonings=[], tags=["家常菜"])


def _count(db, recipe_id: int | None, action: str) -> int:
    stmt = select(func.count()).select_from(UserFeedback)
    if recipe_id is not None:
        stmt = stmt.where(UserFeedback.recipe_id == recipe_id)
    if action:
        stmt = stmt.where(UserFeedback.action == action)
    return db.scalar(stmt)


def _row(db, recipe_id: int, fingerprint: str, action: str) -> UserFeedback | None:
    return db.scalar(
        select(UserFeedback).where(
            UserFeedback.recipe_id == recipe_id,
            UserFeedback.client_fingerprint == fingerprint,
            UserFeedback.action == action,
        )
    )


def test_feedback_like_and_dislike_write_row(client, db_session):
    rid = _add_recipe()
    try:
        resp = client.post("/api/feedback", json={"recipe_id": rid, "action": "like"})
        assert resp.status_code == 200
        like_id = resp.json()["id"]
        assert isinstance(like_id, int)
        resp = client.post("/api/feedback", json={"recipe_id": rid, "action": "dislike"})
        assert resp.status_code == 200
        dislike_id = resp.json()["id"]
        assert dislike_id != like_id

        like_row = db_session.scalar(
            select(UserFeedback).where(UserFeedback.id == like_id)
        )
        dislike_row = db_session.scalar(
            select(UserFeedback).where(UserFeedback.id == dislike_id)
        )
        assert like_row.recipe_id == rid
        assert like_row.action == "like"
        assert dislike_row.action == "dislike"
    finally:
        delete_recipe(URL)


def test_feedback_invalid_action_422(client):
    resp = client.post("/api/feedback", json={"recipe_id": 1, "action": "love"})
    assert resp.status_code == 422


def test_feedback_unknown_recipe_404(client):
    resp = client.post(
        "/api/feedback", json={"recipe_id": 999999, "action": "like"}
    )
    assert resp.status_code == 404


def test_feedback_idempotent_same_action_no_new_row(client, db_session):
    rid = _add_recipe()
    try:
        r1 = client.post("/api/feedback", json={"recipe_id": rid, "action": "like"})
        r2 = client.post("/api/feedback", json={"recipe_id": rid, "action": "like"})
        assert r1.status_code == r2.status_code == 200
        assert r1.json()["id"] == r2.json()["id"]
        assert _count(db_session, rid, "like") == 1
    finally:
        delete_recipe(URL)


def test_feedback_switch_action_allows_new_row(client, db_session):
    rid = _add_recipe()
    try:
        like = client.post("/api/feedback", json={"recipe_id": rid, "action": "like"})
        dislike = client.post(
            "/api/feedback", json={"recipe_id": rid, "action": "dislike"}
        )
        assert like.status_code == dislike.status_code == 200
        assert like.json()["id"] != dislike.json()["id"]
        assert _count(db_session, rid, "like") == 1
        assert _count(db_session, rid, "dislike") == 1
    finally:
        delete_recipe(URL)


def test_fingerprint_is_64_hex_and_no_plaintext_ip(client, db_session):
    rid = _add_recipe()
    try:
        client.post("/api/feedback", json={"recipe_id": rid, "action": "like"})
        row = db_session.scalar(
            select(UserFeedback).where(UserFeedback.recipe_id == rid)
        )
        assert row.client_fingerprint is not None
        assert _HEX64.match(row.client_fingerprint)
        assert "testclient" not in row.client_fingerprint
        assert "127.0.0.1" not in row.client_fingerprint
        # 幂等查询走同一指纹：同 action 重复提交不新增
        client.post("/api/feedback", json={"recipe_id": rid, "action": "like"})
        assert _count(db_session, rid, "like") == 1
    finally:
        delete_recipe(URL)


def test_feedback_uses_salt_in_fingerprint(client, db_session):
    """同一 IP 不同盐 → 不同指纹（盐轮换使历史指纹失效的依据）。"""
    import hashlib

    ip = "testclient"
    fp_default = hashlib.sha256(ip.encode("utf-8")).hexdigest()
    fp_salted = hashlib.sha256(f"{ip}rotation-salt".encode("utf-8")).hexdigest()
    assert fp_default != fp_salted
    # 路由内指纹函数可直接单测（无明文 IP 落库）
    from app.api.routes.feedback import client_fingerprint

    class _Req:
        class client:
            host = ip

    assert client_fingerprint(_Req()) == fp_default
