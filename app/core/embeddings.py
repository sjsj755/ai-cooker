"""EmbeddingProvider 抽象接口。"""

from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    @abstractmethod
    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """批量文本嵌入；P1 采集管线使用。"""
        raise NotImplementedError
