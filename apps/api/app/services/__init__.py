"""应用服务层：把「一次业务动作」编排成对仓储与 agent 的调用序列。

与 routers 的分工：router 只做 HTTP 相关的事（解析入参、翻译异常、定状态码），
业务顺序、降级策略、观测埋点都在这里。
"""

from app.services.generation import (
    GenerationError,
    GenerationOutcome,
    NoExperiencesError,
    ResumeGenerationService,
)

__all__ = [
    "GenerationError",
    "GenerationOutcome",
    "NoExperiencesError",
    "ResumeGenerationService",
]
