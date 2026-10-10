"""Call accounting inherited by parallel tasks; every provider attempt is recorded."""

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from app.schemas.resume import GenerationUsage, UsageCall

_sink: ContextVar[Callable[[dict[str, Any]], Awaitable[None]] | None] = ContextVar(
    "usage_sink", default=None
)
_observer: ContextVar[Any] = ContextVar("call_observer", default=None)
_operation: ContextVar[str] = ContextVar("call_operation", default="llm.complete")


@contextmanager
def usage_scope(sink=None, observability=None, operation: str | None = None) -> Iterator[None]:
    tokens = []
    for var, value in ((_sink, sink), (_observer, observability), (_operation, operation)):
        if value is not None:
            tokens.append((var, var.set(value)))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


def call_observer():
    return _observer.get()


def call_operation() -> str:
    return _operation.get()


async def record_call(call: dict[str, Any]) -> None:
    sink = _sink.get()
    if sink is not None:
        await sink(call)


def summarize_calls(calls: list[dict[str, Any]], *, is_stub=False) -> dict[str, Any]:
    unknown = sum(c.get("input_tokens") is None or c.get("output_tokens") is None for c in calls)
    prices = [c.get("cost_usd") for c in calls]
    return GenerationUsage(
        input_tokens=sum(c.get("input_tokens") or 0 for c in calls),
        output_tokens=sum(c.get("output_tokens") or 0 for c in calls),
        total_tokens=sum(
            (c.get("input_tokens") or 0) + (c.get("output_tokens") or 0) for c in calls
        ),
        latency_ms=round(sum(c.get("latency_ms", 0) for c in calls), 2),
        cost_usd=round(sum(prices), 8) if all(p is not None for p in prices) else None,
        unknown_usage_calls=unknown,
        is_stub=is_stub,
        calls=[UsageCall.model_validate(c) for c in calls],
    ).model_dump(mode="json")
