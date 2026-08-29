"""P6.1 启动预热：RankingService 委托检索器 warmup；lifespan 后台任务开关。"""

import asyncio
import time

from fastapi.testclient import TestClient

import app.main as main_mod
from app.config import Settings
from app.main import create_app
from app.retrieval.ranking import RankingService


class _FakeRetriever:
    def __init__(self) -> None:
        self.calls = 0

    async def warmup(self) -> None:
        self.calls += 1


def test_ranking_service_warmup_delegates():
    retriever = _FakeRetriever()
    service = RankingService(retriever=retriever)
    asyncio.run(service.warmup())
    assert retriever.calls == 1


def test_ranking_service_warmup_skips_without_method():
    class NoWarmup:
        pass

    service = RankingService(retriever=NoWarmup())
    asyncio.run(service.warmup())  # 不抛错


def _wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_startup_warmup_task_runs(monkeypatch):
    calls: list = []

    class _FakeService:
        async def warmup(self) -> None:
            calls.append(1)

    monkeypatch.setattr(
        "app.retrieval.ranking.get_ranking_service", lambda: _FakeService()
    )
    monkeypatch.setattr(
        main_mod, "get_settings", lambda: Settings(warmup_on_startup=True)
    )
    with TestClient(create_app()) as _client:
        assert _wait_until(lambda: len(calls) == 1)


def test_startup_warmup_disabled(monkeypatch):
    calls: list = []

    class _FakeService:
        async def warmup(self) -> None:
            calls.append(1)

    monkeypatch.setattr(
        "app.retrieval.ranking.get_ranking_service", lambda: _FakeService()
    )
    monkeypatch.setattr(
        main_mod, "get_settings", lambda: Settings(warmup_on_startup=False)
    )
    with TestClient(create_app()) as _client:
        time.sleep(0.2)
        assert calls == []
