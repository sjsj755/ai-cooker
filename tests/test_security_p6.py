"""P6 安全加固回归：docs 开关 / ALLOWED_HOSTS 400 / 安全响应头。

create_app 在导入 app.main 时按当时环境变量组装（docs 开关、中间件），
故与限流端到端一致采用子进程隔离，保证环境变量先于导入生效。
"""

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_scenario(env_extra: dict[str, str], code: str) -> subprocess.CompletedProcess:
    env = {**os.environ.copy(), "PYTHONUNBUFFERED": "1", **env_extra}
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )


_ASSERT_HEADERS = r"""
from fastapi.testclient import TestClient

from app.main import app

with TestClient(app) as client:
    for path in ("/health/live", "/"):
        resp = client.get(path)
        headers = resp.headers
        assert headers.get("content-security-policy") == "default-src 'self'", headers
        assert headers.get("x-frame-options") == "DENY", headers
        assert headers.get("x-content-type-options") == "nosniff", headers
        assert headers.get("referrer-policy") == "no-referrer", headers
print("PASS headers")
"""


def test_docs_disabled_404_and_headers_present():
    code = r"""
from fastapi.testclient import TestClient

from app.main import app

with TestClient(app) as client:
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/health/live").status_code == 200
print("PASS docs off")
""" + "\n" + _ASSERT_HEADERS
    result = _run_scenario({"DOCS_ENABLED": "false"}, code)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "PASS docs off" in result.stdout
    assert "PASS headers" in result.stdout


def test_docs_enabled_restores_default():
    code = r"""
from fastapi.testclient import TestClient

from app.main import app

with TestClient(app) as client:
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200
print("PASS docs on")
"""
    result = _run_scenario({"DOCS_ENABLED": "true"}, code)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "PASS docs on" in result.stdout


def test_allowed_hosts_rejects_unmatched_host():
    code = r"""
from fastapi.testclient import TestClient

from app.main import app

with TestClient(app) as client:
    # TestClient 默认 Host=testserver，不在白名单 → 400
    resp = client.get("/health/live")
    assert resp.status_code == 400, resp.status_code
    # 显式携带白名单 Host → 200
    ok = client.get("/health/live", headers={"Host": "example.com"})
    assert ok.status_code == 200, ok.status_code
print("PASS allowed hosts")
"""
    result = _run_scenario(
        {"ALLOWED_HOSTS": "example.com", "DOCS_ENABLED": "false"}, code
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "PASS allowed hosts" in result.stdout


def test_security_headers_can_be_disabled():
    code = r"""
from fastapi.testclient import TestClient

from app.main import app

with TestClient(app) as client:
    headers = client.get("/health/live").headers
    assert "content-security-policy" not in headers
    assert "x-frame-options" not in headers
print("PASS headers off")
"""
    result = _run_scenario({"SECURITY_HEADERS_ENABLED": "false"}, code)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "PASS headers off" in result.stdout
