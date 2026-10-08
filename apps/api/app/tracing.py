"""trace_id 贯穿。

对应 docs/tech-stack.md「代价四 · 跨进程调试」的缓解措施：
前端生成 X-Request-Id 下发给后端，后端原样回写，并把同一个 ID 写进日志。
后续 M8-1 会把该 ID 直接作为 Langfuse trace 的标识。
"""

from __future__ import annotations

import contextvars
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

TRACE_ID_HEADER = "X-Request-Id"

_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="-")

logger = logging.getLogger("app.access")


def current_trace_id() -> str:
    return _trace_id.get()


class TraceIdMiddleware(BaseHTTPMiddleware):
    """为每个请求确定一个 trace_id，并在响应头回写。"""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get(TRACE_ID_HEADER, "").strip()
        trace_id = incoming or uuid.uuid4().hex
        token = _trace_id.set(trace_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # 异常也必须有日志，否则线上只能靠猜
            logger.exception(
                "request_failed method=%s path=%s trace_id=%s",
                request.method,
                request.url.path,
                trace_id,
            )
            raise
        finally:
            _trace_id.reset(token)

        response.headers[TRACE_ID_HEADER] = trace_id
        logger.info(
            "request method=%s path=%s status=%s trace_id=%s duration_ms=%.1f",
            request.method,
            request.url.path,
            response.status_code,
            trace_id,
            (time.perf_counter() - started) * 1000,
        )
        return response


class LoggingConfigurator:
    """占位：M8 接入 Langfuse 时，日志会再挂一层 trace 上下文。"""


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )
