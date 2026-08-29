"""OpenAI 兼容嵌入实现：httpx 异步直调 POST /embeddings。"""

from __future__ import annotations

import httpx

from app.config import Settings
from app.core.embeddings import EmbeddingProvider
from app.core.fallback import FallbackError, retry_with_backoff


class EmbeddingConfigError(RuntimeError):
    """嵌入配置错误（如缺少 API key）。"""


class OpenAICompatibleEmbeddings(EmbeddingProvider):
    """真实 OpenAI 兼容嵌入；按 EMBEDDING_BATCH_SIZE 分批，指数退避重试。"""

    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self._client = client
        if not settings.embedding_api_key:
            raise EmbeddingConfigError("EMBEDDING_API_KEY 未配置，无法执行真实嵌入")

    def _url(self) -> str:
        base = self.settings.embedding_base_url.rstrip("/")
        return f"{base}/embeddings"

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        results: list[list[float]] = []
        batch_size = max(1, self.settings.embedding_batch_size)
        for start in range(0, len(texts), batch_size):
            results.extend(await self._embed_batch(texts[start : start + batch_size]))
        return results

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        if self._client is None:
            async with httpx.AsyncClient(
                timeout=self.settings.embedding_timeout_seconds
            ) as client:
                return await self._post_with_retry(client, texts)
        return await self._post_with_retry(self._client, texts)

    async def _post_with_retry(
        self,
        client: httpx.AsyncClient,
        texts: list[str],
    ) -> list[list[float]]:
        fn = retry_with_backoff(max_attempts=3)(self._post_once)
        try:
            return await fn(client, texts)
        except FallbackError as exc:
            # 数据性错误（如维度不一致）重试无意义，原样抛出
            if isinstance(exc.__cause__, ValueError):
                raise exc.__cause__
            raise

    async def _post_once(
        self,
        client: httpx.AsyncClient,
        texts: list[str],
    ) -> list[list[float]]:
        resp = await client.post(
            self._url(),
            headers={"Authorization": f"Bearer {self.settings.embedding_api_key}"},
            json={"model": self.settings.embedding_model, "input": texts},
        )
        resp.raise_for_status()
        payload = resp.json()
        items = sorted(payload.get("data") or [], key=lambda it: it.get("index", 0))
        vectors = [it["embedding"] for it in items]
        if len(vectors) != len(texts):
            raise ValueError(f"嵌入返回数量不符: {len(vectors)} != {len(texts)}")
        dims = {len(v) for v in vectors}
        if len(dims) != 1:
            raise ValueError(f"嵌入维度不一致: {dims}")
        return vectors
