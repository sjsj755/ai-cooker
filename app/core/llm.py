"""LLMProvider 抽象接口：换模型不改节点逻辑。"""

from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMProvider(ABC):
    """LLM 供应商抽象，兼容 OpenAI / DeepSeek / Qwen 等 OpenAI 风格接口。"""

    @abstractmethod
    async def structured(self, prompt: str, schema: type[T]) -> T:
        """按 schema 强校验的结构化输出；非法输出抛异常由调用方兜底。"""
        raise NotImplementedError
