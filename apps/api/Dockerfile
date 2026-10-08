# syntax=docker/dockerfile:1

# 后端镜像：uv 官方的 python 镜像已带 uv，省掉一层安装
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# ---------- 依赖层：只要 pyproject/lock 没变就命中缓存 ----------
FROM base AS deps
COPY pyproject.toml uv.lock* ./
# --no-install-project：先只装第三方依赖，改业务代码不会让这层失效
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev || uv sync --no-install-project --no-dev

# ---------- 运行层 ----------
FROM base AS runtime
COPY --from=deps /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV="/app/.venv"

COPY . .

EXPOSE 8000

# 用 python -m uvicorn 而不是 uvicorn 可执行文件：避免 PATH 不一致导致找不到
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
