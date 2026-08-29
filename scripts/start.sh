#!/usr/bin/env bash
# P5 生产入口：启动 uvicorn 前 fail-fast 校验，杜绝“校验盲区”。
#
# 校验项：
#   1. FEEDBACK_SALT 必须已设置且非空（开发盐公开可知，生产误用将导致攻击者可
#      反推全部反馈指纹）——缺失即 exit 1，不是 WARN；
#   2. RATE_LIMIT_STORAGE=redis 时 RATE_LIMIT_REDIS_URL 必填；
#   3. WORKERS>1 时必须配 Redis 限流（memory 模式各进程独立计数，无法跨进程一致）。
#
# 用法：
#   FEEDBACK_SALT=... ./scripts/start.sh                 # 正常启动
#   FEEDBACK_SALT=... ./scripts/start.sh --check         # 仅校验不启动（编排预检）
#   WORKERS=4 FEEDBACK_SALT=... RATE_LIMIT_STORAGE=redis \
#     RATE_LIMIT_REDIS_URL=redis://... ./scripts/start.sh
#
# 非 bash 编排（CI/CD）必须在部署编排中实现与下方等价的校验逻辑，或在容器入口点
# 调用本脚本；Windows 开发 / CI 直跑 `uv run uvicorn` 不受影响。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

# 可选加载 .env（不覆盖已导出的环境变量）
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

FEEDBACK_SALT="${FEEDBACK_SALT:-}"
if [ -z "${FEEDBACK_SALT}" ]; then
  echo "[start] ERROR: FEEDBACK_SALT 未设置（生产必填；请使用独立随机盐，禁止使用开发盐）" >&2
  exit 1
fi

RATE_LIMIT_STORAGE="${RATE_LIMIT_STORAGE:-memory}"
if [ "${RATE_LIMIT_STORAGE}" != "memory" ] && [ "${RATE_LIMIT_STORAGE}" != "redis" ]; then
  echo "[start] ERROR: RATE_LIMIT_STORAGE 必须为 memory 或 redis（当前: ${RATE_LIMIT_STORAGE}）" >&2
  exit 1
fi
if [ "${RATE_LIMIT_STORAGE}" = "redis" ] && [ -z "${RATE_LIMIT_REDIS_URL:-}" ]; then
  echo "[start] ERROR: RATE_LIMIT_STORAGE=redis 时必须配置 RATE_LIMIT_REDIS_URL" >&2
  exit 1
fi

WORKERS="${WORKERS:-1}"
if [ "${WORKERS}" -gt 1 ] 2>/dev/null; then
  if [ "${RATE_LIMIT_STORAGE}" != "redis" ]; then
    echo "[start] ERROR: 多 worker 限流必须配 Redis（RATE_LIMIT_STORAGE=redis）" >&2
    exit 1
  fi
  if [ -z "${RATE_LIMIT_REDIS_URL:-}" ]; then
    echo "[start] ERROR: 多 worker + storage=redis 时必须配置 RATE_LIMIT_REDIS_URL" >&2
    exit 1
  fi
fi

if [ "${1:-}" = "--check" ]; then
  echo "[start] 校验通过（FEEDBACK_SALT 已设置；WORKERS=${WORKERS}；RATE_LIMIT_STORAGE=${RATE_LIMIT_STORAGE}）"
  exit 0
fi

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
exec uvicorn app.main:app --host "${HOST}" --port "${PORT}" --workers "${WORKERS}"
