"""P5 限流：配置校验 fail-fast / limiter 装配 / lifespan Redis ping / 429 端到端。"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from slowapi import Limiter

from app.config import Settings
from app.core.rate_limit import build_limiter
from app.main import app

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ---------- 配置校验 fail-fast ----------


def test_settings_redis_storage_requires_url():
    with pytest.raises(ValueError, match="RATE_LIMIT_REDIS_URL"):
        Settings(rate_limit_storage="redis", rate_limit_redis_url="")


def test_settings_invalid_storage_rejected():
    with pytest.raises(ValueError, match="RATE_LIMIT_STORAGE"):
        Settings(rate_limit_storage="mongo")


def test_settings_memory_storage_default_ok():
    settings = Settings(rate_limit_storage="memory")
    assert settings.rate_limit_enabled is False
    assert settings.rate_limit_default_per_minute == 100


# ---------- limiter 装配 ----------


def test_build_limiter_disabled_returns_none():
    assert build_limiter(Settings(rate_limit_enabled=False)) is None


def test_build_limiter_memory():
    limiter = build_limiter(
        Settings(rate_limit_enabled=True, rate_limit_storage="memory")
    )
    assert isinstance(limiter, Limiter)
    assert limiter._storage_uri is None


def test_build_limiter_redis_uses_storage_uri():
    url = "redis://127.0.0.1:6379/0"
    limiter = build_limiter(
        Settings(
            rate_limit_enabled=True,
            rate_limit_storage="redis",
            rate_limit_redis_url=url,
        )
    )
    assert isinstance(limiter, Limiter)
    assert limiter._storage_uri == url


def test_build_limiter_default_limits_from_settings():
    limiter = build_limiter(
        Settings(
            rate_limit_enabled=True,
            rate_limit_default_per_minute=77,
        )
    )
    group = limiter._default_limits[0]
    # slowapi 0.1.10 将 limit 字符串存于 LimitGroup 内部 limit_provider
    assert "77/minute" in group._LimitGroup__limit_provider


# ---------- lifespan Redis ping fail-fast ----------


def test_lifespan_redis_ping_failure_raises(monkeypatch):
    monkeypatch.setattr(
        "app.main.get_settings",
        lambda: Settings(
            rate_limit_enabled=True,
            rate_limit_storage="redis",
            rate_limit_redis_url="redis://127.0.0.1:1/0",
        ),
    )
    with pytest.raises(RuntimeError, match="Redis 健康检查失败"):
        with TestClient(app) as client:
            client.get("/health")


def test_lifespan_redis_ping_success_starts(monkeypatch):
    async def ok_ping(url: str) -> None:
        return None

    monkeypatch.setattr("app.main._redis_ping_fail_fast", ok_ping)
    monkeypatch.setattr(
        "app.main.get_settings",
        lambda: Settings(
            rate_limit_enabled=True,
            rate_limit_storage="redis",
            rate_limit_redis_url="redis://127.0.0.1:6379/0",
        ),
    )
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200


# ---------- 429 端到端（子进程：限流需在导入 app.main 前设好环境变量） ----------


_E2E_CODE = r"""
import json
import os
import sys

os.environ["RATE_LIMIT_ENABLED"] = "true"
os.environ["RATE_LIMIT_STORAGE"] = "memory"
os.environ["RATE_LIMIT_FEEDBACK_PER_MINUTE"] = "3"
os.environ["RATE_LIMIT_RECOMMEND_PER_MINUTE"] = "2"

from fastapi.testclient import TestClient

from app.main import app

with TestClient(app) as client:
    # feedback 桶 3/min：第 4 次应 429 且 JSON 友好
    statuses = [
        client.post(
            "/api/feedback", json={"recipe_id": 999999, "action": "like"}
        ).status_code
        for _ in range(4)
    ]
    assert statuses[:3] == [404, 404, 404], statuses
    assert statuses[3] == 429, statuses
    body = client.post(
        "/api/feedback", json={"recipe_id": 999999, "action": "like"}
    ).json()
    assert body == {"detail": "请求过于频繁，请稍后重试"}, body

    # 默认桶（tags 100/min）与 feedback 独立：feedback 已超桶，tags 仍 200
    assert client.get("/api/tags").status_code == 200

    # recommend 桶（2/min）独立：feedback 超桶不影响 recommend（前 2 次非 429）
    for _ in range(2):
        resp = client.post(
            "/api/recipes/recommend",
            json={"ingredients": ["土豆"], "exclude_tags": []},
        )
        assert resp.status_code != 429, resp.text
    print("PASS 429 端到端")
"""


def test_rate_limit_429_e2e_subprocess():
    result = subprocess.run(
        [sys.executable, "-c", _E2E_CODE],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        env={**os.environ.copy(), "PYTHONUNBUFFERED": "1"},
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "PASS 429 端到端" in result.stdout


_FAILFAST_CODE = r"""
import os

os.environ["RATE_LIMIT_STORAGE"] = "redis"
os.environ.pop("RATE_LIMIT_REDIS_URL", None)

try:
    import app.main  # noqa: F401
except ValueError as exc:
    assert "RATE_LIMIT_REDIS_URL" in str(exc), str(exc)
    print("PASS storage=redis 无 URL 启动报错")
else:
    raise AssertionError("应因配置缺失启动报错")
"""


def test_storage_redis_without_url_fails_fast_subprocess():
    result = subprocess.run(
        [sys.executable, "-c", _FAILFAST_CODE],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ.copy(), "PYTHONUNBUFFERED": "1"},
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "PASS storage=redis 无 URL 启动报错" in result.stdout
