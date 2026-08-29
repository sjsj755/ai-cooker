"""P6 backup.sh 用例：--dry-run 无副作用 / --stop-app 停机窗口门禁。

与 P5 start.sh 一致：无可用 bash（Windows/WSL 未配置）自动跳过；
部署须知要求非 bash 编排实现等价校验或容器入口调用。
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKUP_SH = PROJECT_ROOT / "scripts" / "backup.sh"


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


def _run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    clean = {k: v for k, v in os.environ.copy().items()}
    for key in (
        "BACKUP_STOP_APP_ALLOWED",
        "MYSQL_ROOT_PASSWORD",
        "MYSQL_PASSWORD",
        "BACKUP_DIR",
    ):
        clean.pop(key, None)
    clean.update(env)
    return subprocess.run(
        ["bash", str(BACKUP_SH), *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        env=clean,
    )


@needs_bash
def test_backup_stop_app_requires_allowed_flag():
    result = _run({}, "--stop-app")
    assert result.returncode == 1
    assert "BACKUP_STOP_APP_ALLOWED" in result.stderr


@needs_bash
def test_backup_dry_run_no_side_effects():
    backup_dir = PROJECT_ROOT / "backups"
    before = set(backup_dir.rglob("*")) if backup_dir.exists() else set()
    result = _run(
        {
            "MYSQL_ROOT_PASSWORD": "dummy-for-dry-run",
            "BACKUP_DIR": str(backup_dir),
        },
        "--dry-run",
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "DRY-RUN" in result.stdout
    after = set(backup_dir.rglob("*")) if backup_dir.exists() else set()
    assert after == before, "dry-run 不得产生任何备份产物"


@needs_bash
def test_backup_unknown_arg_rejected():
    result = _run({}, "--bogus")
    assert result.returncode == 2
    assert "未知参数" in result.stderr


@needs_bash
def test_backup_missing_credentials_rejected():
    result = _run({})
    assert result.returncode == 1
    assert "MySQL 凭据" in result.stderr
