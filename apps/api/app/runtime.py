"""进程级运行时信息。"""

from __future__ import annotations

import time
from datetime import UTC, datetime

_PROCESS_STARTED_MONOTONIC = time.monotonic()


def uptime_seconds() -> float:
    return round(time.monotonic() - _PROCESS_STARTED_MONOTONIC, 3)


def utcnow() -> datetime:
    return datetime.now(UTC)
