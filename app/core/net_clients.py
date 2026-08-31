"""进程级共享 httpx.AsyncClient（keep-alive 连接复用）。

LLM 与 Embedding 每次调用不再新建客户端/连接，省去重复 TCP+TLS 握手；
单 worker 语义：每个进程一份，lifespan 关闭时统一释放。
"""

from __future__ import annotations

import httpx

from app.config import Settings, get_settings

_clients: dict[str, httpx.AsyncClient] = {}


def get_llm_http_client(settings: Settings | None = None) -> httpx.AsyncClient:
    """LLM 共享客户端（超时按 LLM_TIMEOUT_SECONDS）。"""
    conf = settings or get_settings()
    return _get_client("llm", conf.llm_timeout_seconds)


def get_embedding_http_client(
    settings: Settings | None = None,
) -> httpx.AsyncClient:
    """Embedding 共享客户端（超时按 EMBEDDING_TIMEOUT_SECONDS）。"""
    conf = settings or get_settings()
    return _get_client("embedding", conf.embedding_timeout_seconds)


def _get_client(kind: str, timeout: float) -> httpx.AsyncClient:
    client = _clients.get(kind)
    if client is None:
        client = httpx.AsyncClient(timeout=timeout)
        _clients[kind] = client
    return client


async def close_http_clients() -> None:
    """关闭全部共享客户端（幂等）；lifespan 关闭阶段调用。"""
    for kind, client in list(_clients.items()):
        await client.aclose()
        _clients.pop(kind, None)
