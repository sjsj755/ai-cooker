"""Docker 入口 MySQL 就绪等待（P6 §3.2）：``SELECT 1`` 重试 30 次 × 2s。

``depends_on: condition: service_healthy`` 只保证“容器已启动”，不保证
“可接受连接”；本脚本在容器入口再做一次应用层就绪探测，全部失败 → 退出码 1
（容器退出，由 ``restart: unless-stopped`` 编排重试，避免“应用已启动但迁移失败”
的竞态）。
"""

from __future__ import annotations

import os
import re
import sys
import time
from urllib.parse import unquote

import pymysql

URL_RE = re.compile(
    r"^mysql(?:\+pymysql)?://(?P<user>[^:]+):(?P<password>[^@]*)@"
    r"(?P<host>[^:/]+):(?P<port>\d+)/(?P<db>[^?]+)"
)

RETRIES = 30
INTERVAL_SECONDS = 2.0


def parse_database_url(url: str) -> dict[str, str]:
    """解析 ``mysql+pymysql://user:pass@host:port/db``；无法解析直接抛 ValueError。"""
    match = URL_RE.match(url)
    if not match:
        raise ValueError(f"无法解析 DATABASE_URL：{url!r}")
    return {
        "user": unquote(match.group("user")),
        "password": unquote(match.group("password")),
        "host": match.group("host"),
        "port": int(match.group("port")),
        "database": match.group("db"),
    }


def wait_for_mysql(
    url: str,
    retries: int = RETRIES,
    interval: float = INTERVAL_SECONDS,
) -> None:
    """循环执行 ``SELECT 1``；全部失败抛 RuntimeError（含最后一次错误）。"""
    cfg = parse_database_url(url)
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            conn = pymysql.connect(
                host=cfg["host"],
                port=cfg["port"],
                user=cfg["user"],
                password=cfg["password"],
                database=cfg["database"],
                charset="utf8mb4",
                connect_timeout=3,
                read_timeout=3,
                write_timeout=3,
            )
            try:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
            finally:
                conn.close()
            print(
                f"[entrypoint] MySQL 就绪（attempt {attempt}/{retries}）",
                flush=True,
            )
            return
        except Exception as exc:
            last_error = exc
            print(
                f"[entrypoint] MySQL 未就绪（attempt {attempt}/{retries}）："
                f"{type(exc).__name__}",
                flush=True,
            )
            if attempt < retries:
                time.sleep(interval)
    raise RuntimeError(
        f"MySQL {retries} 次重试后仍不可用（最后错误：{type(last_error).__name__}: {last_error}）"
    )


def main() -> int:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("[entrypoint] ERROR: DATABASE_URL 未设置", file=sys.stderr)
        return 1
    try:
        wait_for_mysql(url)
    except RuntimeError as exc:
        print(f"[entrypoint] ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
