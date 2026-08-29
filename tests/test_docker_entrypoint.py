"""P6 容器入口：wait_for_mysql 成功/失败/解析 + entrypoint 四阶段契约。

bash 子进程全链用例沿用 P5 模式：无可用 bash（Windows/WSL 未配置）自动跳过；
CI（ubuntu）会真实执行 shim 链验证「预检 → 就绪等待 → 迁移 → exec uvicorn」顺序。
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.wait_for_mysql import parse_database_url, wait_for_mysql

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT_SH = PROJECT_ROOT / "scripts" / "docker-entrypoint.sh"

TEST_URL = (
    "mysql+pymysql://ai_cooker:ai_cooker@127.0.0.1:3306/ai_cooker_test"
)


# ---------- parse_database_url ----------


def test_parse_database_url_ok():
    cfg = parse_database_url(TEST_URL)
    assert cfg["user"] == "ai_cooker"
    assert cfg["password"] == "ai_cooker"
    assert cfg["host"] == "127.0.0.1"
    assert cfg["port"] == 3306
    assert cfg["database"] == "ai_cooker_test"


def test_parse_database_url_rejects_bad_url():
    with pytest.raises(ValueError, match="DATABASE_URL"):
        parse_database_url("postgresql://u:p@h:5432/db")


def test_parse_database_url_unquotes_credentials():
    cfg = parse_database_url("mysql+pymysql://u:p%40ss@h:3306/db")
    assert cfg["password"] == "p@ss"


# ---------- wait_for_mysql：mock pymysql ----------


class _FakeCursor:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql):
        self._sql = sql

    def fetchone(self):
        return (1,)


class _FakeConn:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.closed = False

    def cursor(self):
        return _FakeCursor()

    def close(self):
        self.closed = True


def _install_fake_pymysql(monkeypatch, connect) -> None:
    import types

    fake = types.SimpleNamespace(connect=connect)
    monkeypatch.setattr("scripts.wait_for_mysql.pymysql", fake)


def test_wait_for_mysql_success(monkeypatch):
    calls: list[dict] = []

    def connect(**kwargs):
        calls.append(kwargs)
        return _FakeConn(**kwargs)

    _install_fake_pymysql(monkeypatch, connect)
    monkeypatch.setattr("scripts.wait_for_mysql.time.sleep", lambda _: None)

    wait_for_mysql(TEST_URL, retries=3, interval=0.01)
    assert len(calls) == 1
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["database"] == "ai_cooker_test"


def test_wait_for_mysql_all_fail_exits_with_last_error(monkeypatch):
    attempts = {"n": 0}

    def connect(**kwargs):
        attempts["n"] += 1
        raise ConnectionError(f"refused-{attempts['n']}")

    _install_fake_pymysql(monkeypatch, connect)
    sleeps: list[float] = []
    monkeypatch.setattr("scripts.wait_for_mysql.time.sleep", sleeps.append)

    with pytest.raises(RuntimeError, match="3 次重试后仍不可用"):
        wait_for_mysql(TEST_URL, retries=3, interval=0.5)
    assert attempts["n"] == 3
    assert sleeps == [0.5, 0.5]


def test_wait_for_mysql_recovers_after_failures(monkeypatch):
    attempts = {"n": 0}

    def connect(**kwargs):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ConnectionError("not ready yet")
        return _FakeConn(**kwargs)

    _install_fake_pymysql(monkeypatch, connect)
    monkeypatch.setattr("scripts.wait_for_mysql.time.sleep", lambda _: None)

    wait_for_mysql(TEST_URL, retries=5, interval=0.01)
    assert attempts["n"] == 3


# ---------- entrypoint 静态契约（无 bash 也可执行） ----------


def test_entrypoint_has_four_stage_contract():
    content = ENTRYPOINT_SH.read_text(encoding="utf-8")
    commands = [
        "./scripts/start.sh --check",
        "python /app/scripts/wait_for_mysql.py",
        "alembic upgrade head",
        "exec uvicorn",
    ]
    lines = [line.strip() for line in content.splitlines()]
    positions = [
        next(i for i, line in enumerate(lines) if line.startswith(cmd))
        for cmd in commands
    ]
    assert positions == sorted(positions), "entrypoint 阶段顺序必须为 预检→就绪→迁移→exec"


# ---------- entrypoint 全链（bash 可用环境） ----------


def _bash_available() -> bool:
    path = shutil.which("bash")
    if not path:
        return False
    try:
        result = subprocess.run(
            [path, "--version"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


needs_bash = pytest.mark.skipif(
    not _bash_available(), reason="bash 不可用（Windows/WSL 未配置），跳过脚本用例"
)


@needs_bash
def test_entrypoint_full_chain_order(tmp_path):
    """shim python/alembic/uvicorn，验证执行顺序与成功路径。"""
    log_file = tmp_path / "calls.log"
    shims = tmp_path / "shims"
    shims.mkdir()

    def write_shim(name: str, marker: str) -> None:
        shim = shims / name
        shim.write_text(
            "#!/usr/bin/env bash\n"
            f'printf "%s\\n" "{marker}" >> "{log_file}"\n'
            "exit 0\n",
            encoding="utf-8",
        )
        shim.chmod(0o755)

    write_shim("python", "PYTHON")
    write_shim("alembic", "ALEMBIC")
    write_shim("uvicorn", "UVICORN")

    # 将 /app 硬编码替换为项目根，使本地可跑（容器内保持 /app 不变）
    patched = ENTRYPOINT_SH.read_text(encoding="utf-8").replace("/app", str(PROJECT_ROOT))
    entrypoint = tmp_path / "docker-entrypoint.sh"
    entrypoint.write_text(patched, encoding="utf-8")
    entrypoint.chmod(0o755)

    env = {
        **os.environ.copy(),
        "FEEDBACK_SALT": "test-salt",
        "BEHIND_PROXY": "false",
        "PATH": f"{shims}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    result = subprocess.run(
        ["bash", str(entrypoint)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    calls = log_file.read_text(encoding="utf-8").splitlines()
    # 预检由 start.sh --check 完成（未到 python shim 即代表失败）；顺序为 PYTHON → ALEMBIC → UVICORN
    assert calls == ["PYTHON", "ALEMBIC", "UVICORN"], calls
