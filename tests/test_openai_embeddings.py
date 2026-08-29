"""OpenAI 兼容嵌入：MockTransport 单测（无需真实 key）。"""

import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.core.openai_embeddings import (
    EmbeddingConfigError,
    OpenAICompatibleEmbeddings,
)


def _settings(**kw) -> Settings:
    base = dict(
        embedding_api_key="test-key",
        embedding_model="text-embedding-3-small",
        embedding_batch_size=2,
    )
    base.update(kw)
    return Settings(**base)


def _run(provider_call) -> list[list[float]]:
    return asyncio.run(provider_call)


def test_missing_key_raises():
    with pytest.raises(EmbeddingConfigError):
        OpenAICompatibleEmbeddings(Settings(embedding_api_key=None))


def test_batches_and_index_order():
    calls = []
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        body = json.loads(request.content)
        inputs = body["input"]
        start = counter["n"]
        counter["n"] += len(inputs)
        # 故意乱序返回，验证按 index 对齐
        data = [
            {"embedding": [float(start + i), 0.0], "index": i}
            for i in reversed(range(len(inputs)))
        ]
        return httpx.Response(200, json={"data": data, "model": body["model"]})

    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = OpenAICompatibleEmbeddings(_settings(), client=client)
            return await provider.embed_texts(["a", "b", "c"])

    out = _run(go())
    assert len(out) == 3
    assert out[0] == [0.0, 0.0]
    assert out[1] == [1.0, 0.0]
    assert out[2] == [2.0, 0.0]
    assert calls[0].url.path.endswith("/embeddings")
    assert calls[0].headers["authorization"] == "Bearer test-key"
    assert len(calls) == 2  # batch_size=2 → 2 次请求


def test_retry_on_500_then_success():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(500, text="boom")
        body = json.loads(request.content)
        inputs = body["input"]
        return httpx.Response(
            200,
            json={
                "data": [
                    {"embedding": [float(i)], "index": i}
                    for i in range(len(inputs))
                ]
            },
        )

    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = OpenAICompatibleEmbeddings(_settings(), client=client)
            return await provider.embed_texts(["x"])

    assert _run(go()) == [[0.0]]
    assert attempts["n"] == 2


def test_dimension_mismatch_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        inputs = body["input"]
        return httpx.Response(
            200,
            json={
                "data": [
                    {"embedding": [1.0] if i == 0 else [1.0, 2.0], "index": i}
                    for i in range(len(inputs))
                ]
            },
        )

    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = OpenAICompatibleEmbeddings(_settings(), client=client)
            return await provider.embed_texts(["a", "b"])

    with pytest.raises(ValueError, match="维度不一致"):
        _run(go())
