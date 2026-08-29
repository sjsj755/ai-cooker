#!/usr/bin/env bash
# P6 备份运维：MySQL 无锁备份 + Chroma 目录 tar（以重建为准）+ 停机窗口计时。
#
# 用法：
#   ./scripts/backup.sh                        # 常规备份（不停机）
#   ./scripts/backup.sh --dry-run              # 只打印将执行的命令与目标路径，无副作用
#   BACKUP_STOP_APP_ALLOWED=true ./scripts/backup.sh --stop-app
#                                              # 停机窗口：stop app → 备份 → start app → 轮询 /health/ready
#
# 门禁：
#   - --stop-app 必须显式设置 BACKUP_STOP_APP_ALLOWED=true，否则拒绝执行；
#   - MySQL 凭据（MYSQL_ROOT_PASSWORD 或 MYSQL_PASSWORD）来自 .env，缺失即拒绝；
#   - .env 属敏感配置：仅提示按密码管理流程另行处理，不并入公开备份文件。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

DRY_RUN=false
STOP_APP=false
for arg in "$@"; do
  case "${arg}" in
    --dry-run) DRY_RUN=true ;;
    --stop-app) STOP_APP=true ;;
    *)
      echo "[backup] ERROR: 未知参数 ${arg}（支持 --dry-run / --stop-app）" >&2
      exit 2
      ;;
  esac
done

# 停机窗口门禁：必须在执行任何操作前校验，--dry-run 同样生效
if [ "${STOP_APP}" = "true" ] && [ "${BACKUP_STOP_APP_ALLOWED:-}" != "true" ]; then
  echo "[backup] ERROR: --stop-app 必须显式设置 BACKUP_STOP_APP_ALLOWED=true（停机窗口由运维显式声明）" >&2
  exit 1
fi

# 可选加载 .env（不覆盖已导出的环境变量）
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

BACKUP_DIR="${BACKUP_DIR:-./backups}"
TS="$(date +%Y%m%d_%H%M%S)"
MYSQL_FILE="${BACKUP_DIR}/mysql_${TS}.sql.gz"
CHROMA_FILE="${BACKUP_DIR}/chroma_${TS}.tar.gz"
MANIFEST_FILE="${BACKUP_DIR}/manifest_${TS}.json"

MYSQL_DATABASE="${MYSQL_DATABASE:-ai_cooker}"
MYSQL_PORT="${MYSQL_PORT:-3306}"
MYSQL_USER="${MYSQL_USER:-ai_cooker}"
MYSQL_PASSWORD="${MYSQL_PASSWORD:-}"
MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD:-}"

if [ -n "${MYSQL_ROOT_PASSWORD}" ]; then
  MYSQL_DUMP_USER="root"
  MYSQL_DUMP_PASSWORD="${MYSQL_ROOT_PASSWORD}"
else
  MYSQL_DUMP_USER="${MYSQL_USER}"
  MYSQL_DUMP_PASSWORD="${MYSQL_PASSWORD}"
fi

if [ -z "${MYSQL_DUMP_PASSWORD}" ]; then
  echo "[backup] ERROR: 未找到 MySQL 凭据（.env 需配置 MYSQL_ROOT_PASSWORD 或 MYSQL_PASSWORD）" >&2
  exit 1
fi

if [ "${DRY_RUN}" = "false" ]; then
  mkdir -p "${BACKUP_DIR}"
fi

backup_mysql() {
  if [ "${DRY_RUN}" = "true" ]; then
    echo "[backup] DRY-RUN: mysqldump --single-transaction --set-gtid-purged=OFF --routines --triggers"
    echo "[backup] DRY-RUN:   -u${MYSQL_DUMP_USER} ${MYSQL_DATABASE} | gzip > ${MYSQL_FILE}"
    return 0
  fi
  echo "[backup] MySQL 无锁备份（--single-transaction，不锁表）→ ${MYSQL_FILE}"
  if docker compose ps --quiet mysql >/dev/null 2>&1; then
    docker compose exec -T mysql \
      mysqldump --single-transaction --set-gtid-purged=OFF --routines --triggers \
      -u"${MYSQL_DUMP_USER}" -p"${MYSQL_DUMP_PASSWORD}" "${MYSQL_DATABASE}" \
      | gzip > "${MYSQL_FILE}"
  else
    # 非 compose（裸机 MySQL）兜底：直连 127.0.0.1
    mysqldump --single-transaction --set-gtid-purged=OFF --routines --triggers \
      -h 127.0.0.1 -P "${MYSQL_PORT}" -u"${MYSQL_DUMP_USER}" -p"${MYSQL_DUMP_PASSWORD}" \
      "${MYSQL_DATABASE}" | gzip > "${MYSQL_FILE}"
  fi
}

backup_chroma() {
  if [ ! -d data/chroma ]; then
    echo "[backup] WARN: data/chroma 不存在，跳过 Chroma tar（恢复时以 MySQL + ingest 重建为准）"
    CHROMA_FILE=""
    return 0
  fi
  if [ "${DRY_RUN}" = "true" ]; then
    echo "[backup] DRY-RUN: tar -czf ${CHROMA_FILE} -C ${PROJECT_ROOT} data/chroma"
    return 0
  fi
  echo "[backup] Chroma 目录打包（非强一致，恢复以重建为准）→ ${CHROMA_FILE}"
  tar -czf "${CHROMA_FILE}" -C "${PROJECT_ROOT}" data/chroma
}

START_TIME_MS=0
DOWNTIME_MS=0
if [ "${STOP_APP}" = "true" ]; then
  if [ "${DRY_RUN}" = "true" ]; then
    echo "[backup] DRY-RUN: docker compose stop app（停机窗口开始）"
  else
    echo "[backup] 停止 app 容器（停机窗口开始）"
    docker compose stop app
    START_TIME_MS="$(date +%s%3N)"
  fi
fi

backup_mysql
backup_chroma

if [ "${STOP_APP}" = "true" ]; then
  if [ "${DRY_RUN}" = "true" ]; then
    echo "[backup] DRY-RUN: docker compose start app 后轮询 /health/ready 200（30 次 × 2s）"
    echo "[backup] DRY-RUN: 输出停机时长并写 manifest"
  else
    echo "[backup] 启动 app 容器并轮询 /health/ready（30 次 × 2s）"
    docker compose start app
    ready=false
    for _ in $(seq 1 30); do
      if docker compose exec -T app python -c \
        "import os, urllib.request; req = urllib.request.Request('http://127.0.0.1:8000/health/ready', headers={'Host': os.environ['CADDY_DOMAIN']}); urllib.request.urlopen(req, timeout=3)" \
        >/dev/null 2>&1; then
        ready=true
        break
      fi
      sleep 2
    done
    END_TIME_MS="$(date +%s%3N)"
    DOWNTIME_MS=$((END_TIME_MS - START_TIME_MS))
    if [ "${ready}" != "true" ]; then
      echo "[backup] ERROR: app /health/ready 未在 60s 内恢复（停机 ${DOWNTIME_MS}ms）" >&2
      exit 1
    fi
    echo "[backup] app 已就绪，停机时长 ${DOWNTIME_MS}ms"
  fi
fi

# manifest：JSON 清单（含停机时长）
if [ "${DRY_RUN}" = "true" ]; then
  echo "[backup] DRY-RUN: 写 manifest → ${MANIFEST_FILE}"
else
  CHROMA_JSON="null"
  if [ -n "${CHROMA_FILE}" ]; then
    CHROMA_JSON="\"${CHROMA_FILE}\""
  fi
  {
    echo "{"
    echo "  \"ts\": \"$(date -Iseconds)\","
    echo "  \"stop_app\": ${STOP_APP},"
    echo "  \"downtime_ms\": ${DOWNTIME_MS},"
    echo "  \"mysql\": \"${MYSQL_FILE}\","
    echo "  \"chroma\": ${CHROMA_JSON},"
    echo "  \"note\": \"恢复以 MySQL 回放 + ingest 重建 Chroma 为准；.env 敏感配置不并入公开备份\""
    echo "}"
  } > "${MANIFEST_FILE}"
  echo "[backup] 完成：$(ls -lh "${MYSQL_FILE}" | awk '{print $5}') MySQL + manifest ${MANIFEST_FILE}"
fi
