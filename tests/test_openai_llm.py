"""OpenAI 兼容 LLM：MockTransport 单测（无需真实 key，离线可跑）。"""

import asyncio
import json

import httpx
import pytest
from pydantic import BaseModel

from app.config import Settings
from app.core.openai_llm import (
    LLMConfigError,
    LLMOutputError,
    OpenAICompatibleLLM,
)


class ItemList(BaseModel):
    items: list[str]


def _settings(**kw) -> Settings:
    base = dict(
        llm_base_url="https://api.example.com/v1",
        llm_model="test-model",
        llm_api_key="test-key",
        llm_temperature=0.2,
    )
    base.update(kw)
    return Settings(**base)


def _chat_response(content: str) -> dict:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content,
                }
            }
        ]
    }


def _run(coro) -> ItemList:
    return asyncio.run(coro)


def test_structured_parses_json_and_sends_expected_request():
    calls = []
    payload = {"items": ["西红柿", "鸡蛋"]}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_chat_response(json.dumps(payload, ensure_ascii=False)))

    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            llm = OpenAICompatibleLLM(_settings(), client=client)
            return await llm.structured("识别食材", ItemList)

    result = _run(go())
    assert result.items == ["西红柿", "鸡蛋"]
    assert calls[0].url.path.endswith("/chat/completions")
    assert calls[0].headers["authorization"] == "Bearer test-key"
    body = json.loads(calls[0].content)
    assert body["model"] == "test-model"
    assert body["temperature"] == 0.2
    assert "JSON Schema" in body["messages"][1]["content"]


def test_structured_accepts_fenced_or_prose_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_chat_response(
                "好的，结果如下：\n```json\n{\"items\": [\"土豆\"]}\n```"
            ),
        )

    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            llm = OpenAICompatibleLLM(_settings(), client=client)
            return await llm.structured("识别食材", ItemList)

    assert _run(go()).items == ["土豆"]


def test_retry_on_500_then_success():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json=_chat_response('{"items": ["牛肉"]}'))

    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            llm = OpenAICompatibleLLM(
                _settings(), client=client, max_attempts=3, base_delay=0.01
            )
            return await llm.structured("识别食材", ItemList)

    assert _run(go()).items == ["牛肉"]
    assert attempts["n"] == 2


def test_invalid_json_raises_after_retries():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(200, json=_chat_response("这不是 JSON"))

    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            llm = OpenAICompatibleLLM(
                _settings(), client=client, max_attempts=3, base_delay=0.01
            )
            return await llm.structured("识别食材", ItemList)

    with pytest.raises(LLMOutputError):
        _run(go())
    assert attempts["n"] == 3


def test_schema_mismatch_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response('{"items": "oops"}'))

    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            llm = OpenAICompatibleLLM(
                _settings(), client=client, max_attempts=3, base_delay=0.01
            )
            return await llm.structured("识别食材", ItemList)

    with pytest.raises(LLMOutputError):
        _run(go())


def test_no_api_key_omits_auth_header():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_chat_response('{"items": ["豆腐"]}'))

    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            llm = OpenAICompatibleLLM(_settings(llm_api_key=None), client=client)
            return await llm.structured("识别食材", ItemList)

    assert _run(go()).items == ["豆腐"]
    assert "authorization" not in calls[0].headers


def test_missing_base_url_raises():
    with pytest.raises(LLMConfigError):
        OpenAICompatibleLLM(Settings(llm_base_url=""))
