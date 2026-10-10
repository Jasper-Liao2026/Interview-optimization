"""M8 golden-set prompt comparison.

Examples:
  python scripts/eval_m8.py --stub --out ../../docs/M8-eval.json
  python scripts/eval_m8.py --prompt-a m4.0 --prompt-b m8.0
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.prompts import available_prompt_versions
from app.config import Settings
from app.db import Database
from app.evaluation.golden import golden_set_json
from app.evaluation.pipeline import compare_prompts
from app.evaluation.prompt_registry import list_prompt_versions
from app.evaluation.store import EvaluationStore


async def main(args: argparse.Namespace) -> int:
    settings = Settings()
    if args.stub:
        settings = settings.model_copy(update={"llm_provider": "stub", "judge_provider": "stub"})
    versions = available_prompt_versions()
    if args.prompt_a not in versions or args.prompt_b not in versions:
        raise SystemExit(f"prompt 版本必须来自 {versions}")
    report = await compare_prompts(
        settings,
        prompt_a=args.prompt_a,
        prompt_b=args.prompt_b,
        concurrency=args.concurrency,
        allow_stub=args.stub,
        min_score=args.min_score,
        max_regression=args.max_regression,
    )
    payload = report.model_dump()
    if args.persist_dataset:
        # Dataset persistence is intentionally separate from evaluation output;
        # callers can run this command offline and upload the JSON later.
        payload["dataset"] = golden_set_json()
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(
        output.write_text,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if not args.no_persist:
        database = Database(settings)
        try:
            store = EvaluationStore(database)
            await store.save_prompt_snapshots(list_prompt_versions())
            dataset_id = await store.save_dataset(report.dataset_version, golden_set_json())
            run_id = await store.save_run(dataset_id, payload)
            payload["dataset_id"], payload["run_id"] = str(dataset_id), str(run_id)
            await asyncio.to_thread(
                output.write_text,
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        finally:
            await database.close()
    print(
        f"M8 prompt evaluation: mode={report.mode}, cases={len(report.cases)}, "
        f"averages={report.averages}, delta={report.delta}; report={output}"
    )
    return 0 if report.passed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt-a", default="m4.0")
    parser.add_argument("--prompt-b", default="m8.0")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--stub", action="store_true", help="仅运行显式标记的 stub 冒烟评测")
    parser.add_argument("--persist-dataset", action="store_true")
    parser.add_argument("--no-persist", action="store_true", help="不写 Postgres（离线模式）")
    parser.add_argument("--min-score", type=float, default=0)
    parser.add_argument("--max-regression", type=float, default=0)
    parser.add_argument("--out", type=Path, default=Path("../../docs/M8-eval.json"))
    options = parser.parse_args()
    if options.concurrency < 1:
        parser.error("--concurrency must be positive")
    raise SystemExit(asyncio.run(main(options)))
