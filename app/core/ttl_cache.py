"""通用内存 TTL 缓存（单进程 uvicorn 使用；多 worker 时各进程独立缓存）。

线程安全（FastAPI 异步处理器在事件循环内单线程执行，加锁仅为防御性）；
按 LRU 顺序淘汰最旧条目，写入时顺带清理过期项。
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any


class TTLCache:
    def __init__(self, ttl_seconds: float, max_entries: int = 256) -> None:
        self._ttl = max(float(ttl_seconds), 0.0)
        self._max_entries = max(1, int(max_entries))
        self._data: OrderedDict[Any, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._ttl > 0.0

    def get(self, key: Any) -> Any | None:
        if not self.enabled:
            return None
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            expires, value = item
            if expires <= time.monotonic():
                del self._data[key]
                return None
            self._data.move_to_end(key)  # LRU：命中后移到队尾
            return value

    def set(self, key: Any, value: Any) -> None:
        if not self.enabled:
            return
        with self._lock:
            now = time.monotonic()
            for k in [k for k, (exp, _) in self._data.items() if exp <= now]:
                del self._data[k]
            self._data[key] = (now + self._ttl, value)
            self._data.move_to_end(key)
            while len(self._data) > self._max_entries:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
