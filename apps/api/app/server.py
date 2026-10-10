"""本地服务启动入口。

Windows 上 psycopg 的异步连接需要 SelectorEventLoop；直接用 Uvicorn 的默认
ProactorEventLoop 会在第一次访问 Postgres 时失败。这个入口为 Uvicorn 提供
兼容的事件循环工厂，同时保留 host、port 和 reload 参数。
Linux/Docker 仍可直接使用原有的 ``uvicorn app.main:app`` 命令。
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import uvicorn


def selector_event_loop() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop()


def main() -> None:
    parser = argparse.ArgumentParser(description="启动简历优化器 API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        loop="app.server:selector_event_loop" if sys.platform == "win32" else "auto",
    )


if __name__ == "__main__":
    main()
