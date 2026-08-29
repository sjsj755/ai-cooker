"""食材联想：LIKE 不足时向量补充合并去重，失败回退 LIKE-only。"""

import asyncio

from sqlalchemy import select

from app.api.deps import get_embedding_provider, get_ingredients_chroma
from app.db.session import SessionLocal
from app.main import app
from app.models import Ingredient
from app.vector_store import ChromaStore
from tests.conftest import FakeEmbeddings


def _setup(tmp_path):
    with SessionLocal() as session:
        if session.scalar(select(Ingredient).where(Ingredient.name == "芋头")) is None:
            session.add(Ingredient(name="芋头", aliases=["香芋"]))
            session.commit()
    with SessionLocal() as session:
        tudi = session.scalar(select(Ingredient).where(Ingredient.name == "土豆"))
        yutou = session.scalar(select(Ingredient).where(Ingredient.name == "芋头"))
    embeddings = FakeEmbeddings()
    store = ChromaStore(path=str(tmp_path / "chroma"), collection="ingredients_docs")
    ids = [str(tudi.id), str(yutou.id)]
    texts = ["土豆 马铃薯 洋芋", "芋头 香芋"]
    metas = [
        {"ingredient_id": tudi.id, "name": "土豆"},
        {"ingredient_id": yutou.id, "name": "芋头"},
    ]
    vectors = asyncio.run(embeddings.embed_texts(texts))
    asyncio.run(store.upsert(ids=ids, documents=texts, metadatas=metas, embeddings=vectors))
    return store, embeddings, yutou.id


def _cleanup(yutou_id: int) -> None:
    with SessionLocal() as session:
        row = session.get(Ingredient, yutou_id)
        if row is not None:
            session.delete(row)
            session.commit()


def _clear_overrides() -> None:
    app.dependency_overrides.pop(get_embedding_provider, None)
    app.dependency_overrides.pop(get_ingredients_chroma, None)


def test_vector_fills_when_like_insufficient(client, tmp_path):
    store, embeddings, yutou_id = _setup(tmp_path)
    try:
        app.dependency_overrides[get_embedding_provider] = lambda: embeddings
        app.dependency_overrides[get_ingredients_chroma] = lambda: store
        resp = client.get("/api/ingredients/search", params={"q": "马铃薯", "limit": 5})
        assert resp.status_code == 200
        names = [item["name"] for item in resp.json()]
        assert names[0] == "土豆"
        assert "芋头" in names
        assert names.count("土豆") == 1  # 去重
    finally:
        _clear_overrides()
        _cleanup(yutou_id)


def test_like_only_when_no_embedding(client, tmp_path):
    store, _embeddings, yutou_id = _setup(tmp_path)
    try:
        app.dependency_overrides[get_embedding_provider] = lambda: None
        app.dependency_overrides[get_ingredients_chroma] = lambda: store
        resp = client.get("/api/ingredients/search", params={"q": "马铃薯", "limit": 5})
        assert resp.status_code == 200
        names = [item["name"] for item in resp.json()]
        assert names == ["土豆"]
    finally:
        _clear_overrides()
        _cleanup(yutou_id)


def test_like_only_fallback_on_embed_failure(client, tmp_path):
    store, _embeddings, yutou_id = _setup(tmp_path)
    try:
        app.dependency_overrides[get_embedding_provider] = lambda: FakeEmbeddings(fail=True)
        app.dependency_overrides[get_ingredients_chroma] = lambda: store
        resp = client.get("/api/ingredients/search", params={"q": "马铃薯", "limit": 5})
        assert resp.status_code == 200
        names = [item["name"] for item in resp.json()]
        assert names == ["土豆"]
    finally:
        _clear_overrides()
        _cleanup(yutou_id)
