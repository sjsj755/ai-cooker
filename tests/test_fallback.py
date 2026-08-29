"""兜底框架：重试超限抛 FallbackError；DegradedResult 标记正确。"""

import pytest

from app.core.fallback import (
    DegradedResult,
    FallbackError,
    degrade,
    retry_with_backoff,
)


def test_retry_with_backoff_raises_after_max_attempts():
    calls = {"n": 0}

    @retry_with_backoff(max_attempts=3, base_delay=0.01)
    async def always_fail():
        calls["n"] += 1
        raise ValueError("boom")

    with pytest.raises(FallbackError):
        import asyncio

        asyncio.run(always_fail())
    assert calls["n"] == 3


def test_retry_with_backoff_succeeds():
    calls = {"n": 0}

    @retry_with_backoff(max_attempts=3, base_delay=0.01)
    async def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise TimeoutError("retry")
        return "ok"

    import asyncio

    assert asyncio.run(flaky()) == "ok"
    assert calls["n"] == 2


def test_degraded_result_defaults():
    result = DegradedResult()
    assert result.degraded is False
    assert result.data is None
    assert result.notice is None


def test_degrade_flags():
    result = degrade("AI 文案不可用", data={"partial": True})
    assert result.degraded is True
    assert result.notice == "AI 文案不可用"
    assert result.data == {"partial": True}
