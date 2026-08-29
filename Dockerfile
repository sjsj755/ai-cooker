# P6 应用镜像：python:3.14-slim + uv --frozen --no-dev + 代码/迁移/前端
FROM python:3.14-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# 先装依赖再拷代码：利用 Docker 层缓存，代码变更不重复解析依赖
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
COPY frontend ./frontend
COPY scripts ./scripts

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# 入口：预检（start.sh --check）→ MySQL 就绪等待 → 迁移 → exec uvicorn
ENTRYPOINT ["bash", "/app/scripts/docker-entrypoint.sh"]
