#!/usr/bin/env bash
# P6 容器入口（app 镜像 ENTRYPOINT）：
#   1. 预检：复用 scripts/start.sh --check（FEEDBACK_SALT / Redis / 反代白名单 / 多 worker），
#      容器入口即“非 bash 编排”的等价校验承接点（P5 §9.1 / P6 §9.4）；
#   2. MySQL 就绪：SELECT 1 重试 30 次 × 2s（depends_on service_healthy 之外的应用层双保险）；
#   3. alembic upgrade head：迁移成功才继续（幂等，重复启动不重建）；
#   4. exec uvicorn：替换 shell 进程，信号直达容器。
set -euo pipefail

cd /app

./scripts/start.sh --check

python /app/scripts/wait_for_mysql.py

alembic upgrade head

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
WORKERS="${WORKERS:-1}"
exec uvicorn app.main:app --host "${HOST}" --port "${PORT}" --workers "${WORKERS}"
