"""把 FastAPI 的 OpenAPI schema 导出为文件，供 openapi-typescript 消费。

为什么不用「起服务再 curl /openapi.json」：
  1) 生成类型不需要一个活着的服务，CI 里少一步拉起容器
  2) 直接 import app 拿到的 schema 与实际部署的代码 100% 同源
  3) 不用处理端口占用、CORS、启动超时

用法（在 apps/api 目录下）：
    uv run python scripts/export_openapi.py
    uv run python scripts/export_openapi.py --out openapi/openapi.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 允许 `python scripts/export_openapi.py` 这种从 apps/api 直接跑的方式
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app

DEFAULT_OUT = Path("openapi/openapi.json")


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 OpenAPI schema")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="输出路径")
    args = parser.parse_args()

    schema = app.openapi()

    out_path: Path = args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # sort_keys + 固定缩进：让 diff 稳定，CI 里比对「类型是否同步」才有意义
    out_path.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    paths = sorted(schema.get("paths", {}).keys())
    print(f"[openapi] {out_path}  ({len(paths)} paths)")
    for path in paths:
        print(f"          - {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
