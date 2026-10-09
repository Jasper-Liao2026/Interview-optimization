"""FastAPI 应用入口。

职责边界（tech-stack.md）：
前端只做 UI 渲染与流式消费，**全部业务逻辑与 agent 编排都在这里**。
本文件只负责装配：中间件 → 路由 → 生命周期，不放任何业务代码。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.config import get_settings
from app.db import get_database
from app.observability import get_observability
from app.routers import health, observability, system
from app.tracing import TraceIdMiddleware, configure_logging, current_trace_id

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    observability_client = get_observability()
    logger.info(
        "startup service=%s version=%s env=%s milestone=%s langfuse=%s llm_provider=%s",
        settings.service_id,
        __version__,
        settings.environment,
        settings.milestone,
        "on" if observability_client.enabled else "off",
        settings.llm_provider,
    )
    # 刻意不在这里连数据库：postgres 慢启动不应阻塞 api 起来。
    # 连接池在第一次真正用到时才建（见 app/db.py）。
    yield
    # 退出前把缓冲里的 trace 推出去，否则最后一批 span 会丢
    observability_client.shutdown()
    await get_database().close()
    logger.info("shutdown complete")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="简历优化器 API",
        version=__version__,
        summary="批量生产岗位适配版简历",
        description=(
            "后端承载全部业务逻辑与 agent 编排。\n\n"
            "**接口类型的单一定义源是本服务的 Pydantic model**，"
            "前端 TypeScript 类型由 `openapi-typescript` 自动生成，禁止手写。"
        ),
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
        # FastAPI 0.142+ 自带原生 OTel：一旦检测到全局 TracerProvider（Langfuse SDK 会设），
        # 它就会给**每个请求**自动开一条 `fastapi.*` trace，把我们在 Langfuse 里
        # 精心命名的业务 trace 冲散成一堆噪声。观测由 Langfuse SDK 统一负责，
        # 因此这里整体关掉 FastAPI 的原生埋点，只保留我们显式写的 span/generation。
        telemetry={
            "tracing": False,
            "metrics": False,
            "logs": False,
            "auto_configure": False,
        },
    )

    # --- 中间件 ---
    # CORS：依据 tech-stack.md「代价二」，浏览器直连后端，因此必须放开前端源
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id"],
    )
    app.add_middleware(TraceIdMiddleware)

    # --- 路由 ---
    # /health 供容器 healthcheck，不带版本前缀
    app.include_router(health.router)
    # 业务接口统一走 /api/v1
    app.include_router(health.router, prefix=settings.api_prefix)
    app.include_router(system.router, prefix=settings.api_prefix)
    app.include_router(observability.router, prefix=settings.api_prefix)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """兜底异常处理：任何未捕获异常都带上 trace_id 再返回，便于捞日志。"""
        logger.exception("unhandled error path=%s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "detail": "服务器内部错误",
                "code": "internal_error",
                "trace_id": current_trace_id(),
            },
        )

    return app


app = create_app()
