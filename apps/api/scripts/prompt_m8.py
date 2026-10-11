"""Inspect or persist the prompt version used on the next API startup."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.db import Database, DatabaseUnavailable
from app.evaluation.prompt_registry import PromptVersionRepository, list_prompt_versions


async def main(args: argparse.Namespace) -> int:
    settings = Settings()
    database = Database(settings)
    registry = PromptVersionRepository(database, settings=settings)
    try:
        if args.command == "list":
            selected = await registry.selected()
            print(json.dumps({
                "selected_version": selected,
                "precedence": "database selection > PROMPT_VERSION",
                "versions": [
                    {"version": item["version"], "content_hash": item["content_hash"]}
                    for item in list_prompt_versions()
                ],
            }, ensure_ascii=False, indent=2))
        else:
            if args.command == "select":
                row = await registry.select(args.version, actor=args.actor)
            else:
                row = await registry.rollback(actor=args.actor)
            print(json.dumps(row, ensure_ascii=False, indent=2, default=str))
            print("已保存；重启 API 后，新任务使用此版本。恢复任务继续使用原版本。")
        return 0
    except (ValueError, DatabaseUnavailable) as exc:
        print(f"prompt 配置失败：{exc}", file=sys.stderr)
        return 1
    finally:
        await database.close()


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="列出版本和数据库/环境变量决定的选择")
    select = commands.add_parser("select", help="持久化选择一个版本")
    select.add_argument("version")
    select.add_argument("--actor", default="prompt-cli")
    rollback = commands.add_parser("rollback", help="持久化恢复前一个版本")
    rollback.add_argument("--actor", default="prompt-cli")
    return cli


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(parser().parse_args())))
