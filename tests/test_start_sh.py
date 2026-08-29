"""P5/P6 start.sh 子进程用例：FEEDBACK_SALT / Redis / 反代白名单 / 非 bash 等价校验。

无可用 bash 的环境自动跳过，并在 docs/P5_PLAN.md §8 记录原因；
部署须知覆盖“非 bash 编排等价校验或容器入口调用 start.sh”。
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
START_SH = PROJECT_ROOT / "scripts" / "start.sh"


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


_BASH_OK = _bash_available()
needs_bash = pytest.mark.skipif(
    not _BASH_OK, reason="bash 不可用（Windows/WSL 未配置），跳过脚本用例"
)


def _run(env: dict[str, str]) -> subprocess.CompletedProcess:
    clean = {k: v for k, v in os.environ.copy().items()}
    # 清除可能干扰的 P5 变量，保证用例从干净状态开始
    for key in (
        "FEEDBACK_SALT",
        "RATE_LIMIT_STORAGE",
        "RATE_LIMIT_REDIS_URL",
        "WORKERS",
        "BEHIND_PROXY",
        "FORWARDED_ALLOW_IPS",
    ):
        clean.pop(key, None)
    clean.update(env)
    return subprocess.run(
        ["bash", str(START_SH), "--check"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        env=clean,
    )


@needs_bash
def test_start_sh_fails_without_salt():
    result = _run({})
    assert result.returncode == 1
    assert "FEEDBACK_SALT" in result.stderr


@needs_bash
def test_start_sh_fails_workers_without_redis():
    result = _run({"FEEDBACK_SALT": "test-salt", "WORKERS": "2"})
    assert result.returncode == 1
    assert "Redis" in result.stderr


@needs_bash
def test_start_sh_fails_redis_storage_without_url():
    result = _run(
        {"FEEDBACK_SALT": "test-salt", "RATE_LIMIT_STORAGE": "redis"}
    )
    assert result.returncode == 1
    assert "RATE_LIMIT_REDIS_URL" in result.stderr


@needs_bash
def test_start_sh_check_passes_when_configured():
    result = _run({"FEEDBACK_SALT": "test-salt", "WORKERS": "1"})
    assert result.returncode == 0
    assert "校验通过" in result.stdout


@needs_bash
def test_start_sh_fails_behind_proxy_without_allow_ips():
    """P6：BEHIND_PROXY=true 且 FORWARDED_ALLOW_IPS 为空 → exit 1（拒绝启动而非 WARN）。"""
    result = _run({"FEEDBACK_SALT": "test-salt", "BEHIND_PROXY": "true"})
    assert result.returncode == 1
    assert "FORWARDED_ALLOW_IPS" in result.stderr


@needs_bash
def test_start_sh_passes_behind_proxy_with_allow_ips():
    result = _run(
        {
            "FEEDBACK_SALT": "test-salt",
            "BEHIND_PROXY": "true",
            "FORWARDED_ALLOW_IPS": "172.28.0.10",
        }
    )
    assert result.returncode == 0
    assert "校验通过" in result.stdout


@needs_bash
def test_start_sh_not_enforced_direct_mode():
    """BEHIND_PROXY=false 时不强制白名单（本地直连开发）。"""
    result = _run({"FEEDBACK_SALT": "test-salt", "BEHIND_PROXY": "false"})
    assert result.returncode == 0
    assert "校验通过" in result.stdout


@needs_bash
def test_start_sh_passes_redis_multi_worker_configured():
    result = _run(
        {
            "FEEDBACK_SALT": "test-salt",
            "WORKERS": "4",
            "RATE_LIMIT_STORAGE": "redis",
            "RATE_LIMIT_REDIS_URL": "redis://127.0.0.1:6379/0",
        }
    )
    assert result.returncode == 0
    assert "校验通过" in result.stdout
