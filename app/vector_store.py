"""Chroma 向量库封装：recipe_docs 集合，幂等 upsert。"""

from __future__ import annotations

import asyncio
from functools import lru_cache

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.config import Settings


class ChromaDimensionError(RuntimeError):
    """集合维度与本次嵌入不一致（换模型/需清空集合）。"""


@lru_cache
def get_chroma_client(path: str) -> chromadb.ClientAPI:
    """按路径缓存单例客户端（单写者约定：CLI 与 uvicorn 勿同时写同一目录）。"""
    return chromadb.PersistentClient(
        path=path,
        settings=ChromaSettings(anonymized_telemetry=False),
    )


class ChromaStore:
    """本地向量库：幂等 upsert、count、heartbeat。"""

    def __init__(
        self,
        settings: Settings | None = None,
        path: str | None = None,
        collection: str | None = None,
    ) -> None:
        self._path = path or (settings.chroma_dir if settings else "./data/chroma")
        self._collection_name = collection or (
            settings.chroma_collection if settings else "recipe_docs"
        )
        self._client = get_chroma_client(self._path)
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def count(self) -> int:
        return self._collection.count()

    def heartbeat(self) -> float:
        return self._client.heartbeat()

    async def upsert(
        self,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict],
        embeddings: list[list[float]],
    ) -> None:
        if not ids:
            return
        await asyncio.to_thread(
            self._upsert_sync, ids, documents, metadatas, embeddings
        )

    async def delete_where(self, where: dict) -> None:
        """删除匹配元数据过滤条件的全部块（写入前清理旧块）。"""
        await asyncio.to_thread(self._delete_where_sync, where)

    async def get_chunk_metadata(self, where: dict) -> list[dict]:
        """按过滤条件取块元数据（测试与 P2 过滤用）。"""
        return await asyncio.to_thread(self._get_chunk_metadata_sync, where)

    def _upsert_sync(
        self,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict],
        embeddings: list[list[float]],
    ) -> None:
        try:
            self._collection.upsert(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
                embeddings=embeddings,
            )
        except Exception as exc:  # noqa: BLE001 - 统一转维度冲突为明确错误
            message = str(exc).lower()
            if "dimensionality" in message or "dimension" in message:
                raise ChromaDimensionError(
                    f"Chroma 集合维度与嵌入不一致：{exc}；"
                    f"换嵌入模型/维度需清空 {self._collection_name} 集合后重跑"
                ) from exc
            raise

    def _delete_where_sync(self, where: dict) -> None:
        self._collection.delete(where=where)

    def _get_chunk_metadata_sync(self, where: dict) -> list[dict]:
        result = self._collection.get(where=where, include=["metadatas"])
        return list(result.get("metadatas") or [])
