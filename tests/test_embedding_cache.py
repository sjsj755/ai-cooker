"""P6.4 embedding 查询缓存：同文本只调一次、键含模型名、可关闭。"""

import asyncio

import app.core.openai_embeddings as emb_mod
from app.config import Settings
from app.core.ttl_cache import TTLCache


class _FakeResponse:
    def raise_for_status(self):
        pass

    def json(self):
        return {"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]}


class _FakeClient:
    def __init__(self):
        self.posts = 0

    async def post(self, *args, **kwargs):
        self.posts += 1
        return _FakeResponse()


def _provider(client, model="m1"):
    return emb_mod.OpenAICompatibleEmbeddings(
        Settings(
            embedding_api_key="test-key",
            embedding_model=model,
            embedding_batch_size=64,
        ),
        client=client,
    )


def test_embedding_cache_reuses_same_text(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(emb_mod, "_embedding_cache", TTLCache(ttl_seconds=60))
    provider = _provider(client)
    v1 = asyncio.run(provider.embed_texts(["番茄"]))
    v2 = asyncio.run(provider.embed_texts(["番茄"]))
    assert client.posts == 1
    assert v1 == v2 == [[0.1, 0.2, 0.3]]


def test_embedding_cache_key_includes_model(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(emb_mod, "_embedding_cache", TTLCache(ttl_seconds=60))
    asyncio.run(_provider(client, model="m1").embed_texts(["番茄"]))
    asyncio.run(_provider(client, model="m2").embed_texts(["番茄"]))
    assert client.posts == 2  # 模型不同不共享缓存


def test_embedding_cache_disabled_posts_every_time(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(emb_mod, "_embedding_cache", TTLCache(ttl_seconds=0))
    provider = _provider(client)
    asyncio.run(provider.embed_texts(["番茄"]))
    asyncio.run(provider.embed_texts(["番茄"]))
    assert client.posts == 2
