"""观测层（M0-8）。

对外只暴露 `get_observability()` 与两个上下文管理器 `span()` / `generation()`，
业务代码不直接碰 Langfuse SDK —— 这样换观测后端、或在未配置时降级，都只改这一处。
"""

from app.observability.langfuse_client import (
    Observability,
    get_observability,
    normalize_trace_id,
)

__all__ = ["Observability", "get_observability", "normalize_trace_id"]
