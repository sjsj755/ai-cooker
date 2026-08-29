"""OpenAI 兼容 LLM 实现：httpx 异步直调 POST /chat/completions，结构化输出。

兼容 OpenAI / DeepSeek / Qwen / Ollama 等任意 OpenAI 风格端点：
- LLM_BASE_URL 可切换服务商，模型名由 LLM_MODEL 指定；
- LLM_API_KEY 为空时不带 Authorization 头（适配 Ollama / LM Studio 等本地端点）；
- 输出按 JSON Schema 提示生成，经代码块剥离 + 花括号扫描提取后由 pydantic 强校验；
- 不依赖 response_format，最大化服务商兼容性。
"""

from __future__ import annotations

import json
import re
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.core.fallback import FallbackError, retry_with_backoff
from app.core.llm import LLMProvider

T = TypeVar("T", bound=BaseModel)

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


class LLMConfigError(RuntimeError):
    """LLM 配置错误（如 base_url 为空）。"""


class LLMOutputError(ValueError):
    """模型输出无法解析/匹配 schema（重试后仍失败）。"""


class OpenAICompatibleLLM(LLMProvider):
    """真实 OpenAI 兼容 LLM；重试沿用 retry_with_backoff 兜底框架。"""

    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        *,
        max_attempts: int = 3,
        base_delay: float = 0.5,
    ) -> None:
        self.settings = settings
        self._client = client
        self._max_attempts = max_attempts
        self._base_delay = base_delay
        if not settings.llm_base_url:
            raise LLMConfigError("LLM_BASE_URL 未配置")

    def _url(self) -> str:
        base = self.settings.llm_base_url.rstrip("/")
        return f"{base}/chat/completions"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.llm_api_key:
            headers["Authorization"] = f"Bearer {self.settings.llm_api_key}"
        return headers

    def _build_body(self, prompt: str, schema: type[T]) -> dict[str, Any]:
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        messages = [
            {
                "role": "system",
                "content": (
                    "你只输出合法 JSON，不输出 Markdown 代码块或任何额外文字；"
                    "字段名与类型必须严格符合用户给定的 JSON Schema。"
                ),
            },
            {
                "role": "user",
                "content": f"JSON Schema：\n{schema_json}\n\n任务：\n{prompt}",
            },
        ]
        return {
            "model": self.settings.llm_model,
            "messages": messages,
            "temperature": self.settings.llm_temperature,
        }

    async def structured(self, prompt: str, schema: type[T]) -> T:
        body = self._build_body(prompt, schema)
        fn = retry_with_backoff(
            max_attempts=self._max_attempts, base_delay=self._base_delay
        )(self._post_once)
        try:
            return await fn(body, schema)
        except FallbackError as exc:
            # 数据性错误（输出无法解析/匹配 schema）重试无意义，原样抛出
            if isinstance(exc.__cause__, ValueError):
                raise exc.__cause__
            raise

    async def _post_once(self, body: dict[str, Any], schema: type[T]) -> T:
        if self._client is not None:
            return await self._request(self._client, body, schema)
        async with httpx.AsyncClient(
            timeout=self.settings.llm_timeout_seconds
        ) as client:
            return await self._request(client, body, schema)

    async def _request(
        self,
        client: httpx.AsyncClient,
        body: dict[str, Any],
        schema: type[T],
    ) -> T:
        resp = await client.post(self._url(), headers=self._headers(), json=body)
        resp.raise_for_status()
        payload = resp.json()
        content = _extract_content(payload)
        try:
            return schema.model_validate_json(_json_from_content(content))
        except ValidationError as exc:
            raise LLMOutputError(
                f"模型输出无法匹配 schema {schema.__name__}: {exc}"
            ) from exc


def _extract_content(payload: dict) -> str:
    """从 chat/completions 响应中取出 message.content（兼容文本块/部分服务端字段）。"""
    choices = payload.get("choices") or []
    if not choices:
        raise LLMOutputError("响应缺少 choices 字段")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if content is None:
        content = message.get("text")  # 部分服务端兼容字段
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                parts.append(str(block.get("text") or block.get("content") or ""))
            else:
                parts.append(str(block))
        content = "".join(parts)
    if not isinstance(content, str) or not content.strip():
        raise LLMOutputError("响应 message.content 为空或类型异常")
    return content


def _json_from_content(content: str) -> str:
    """剥离 Markdown 代码块，扫描首个平衡花括号 JSON 对象。"""
    text = content.strip()
    fence = _JSON_FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    if start < 0:
        raise LLMOutputError("模型输出中未找到 JSON 对象")
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise LLMOutputError("模型输出中 JSON 对象未闭合")
