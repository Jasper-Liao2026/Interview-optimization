"""M8 acceptance: Postgres dataset/run persistence and prompt rollback."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.prompts import get_prompt_snapshot
from app.config import Settings
from app.db import Database
from app.evaluation.golden import golden_set_json
from app.evaluation.pipeline import compare_prompts
from app.evaluation.prompt_registry import PromptVersionRepository, list_prompt_versions
from app.evaluation.store import EvaluationStore


async def main(no_cleanup: bool = False) -> int:
    settings = Settings(llm_provider="stub", judge_provider="stub")
    database = Database(settings)
    checks = 0
    dataset_id = run_id = None
    original_settings = None
    try:
        report = await compare_prompts(settings, allow_stub=True, min_score=0)
        assert report.mode == "stub" and len(report.cases) == 40
        checks += 1
        store = EvaluationStore(database)
        await store.save_prompt_snapshots(list_prompt_versions())
        dataset_version = f"{report.dataset_version}-verify-{uuid4().hex[:8]}"
        dataset_id = await store.save_dataset(dataset_version, golden_set_json())
        assert dataset_id == await store.save_dataset(dataset_version, golden_set_json())
        changed = golden_set_json()
        changed[0]["raw_jd"] += " changed"
        try:
            await store.save_dataset(dataset_version, changed)
        except ValueError:
            checks += 1
        else:
            raise AssertionError("dataset 内容冲突必须被拒绝")
        run_id = await store.save_run(dataset_id, report.model_dump())
        checks += 1
        async with database.connection() as conn:
            row = await conn.fetchrow(
                "select version,cases from evaluation_datasets where id=$1", dataset_id
            )
            run = await conn.fetchrow("select mode,result from evaluation_runs where id=$1", run_id)
        assert row and len(row["cases"]) == 20 and run and run["mode"] == "stub"
        checks += 1

        registry = PromptVersionRepository(database, settings=settings)
        async with database.connection() as conn:
            saved = await conn.fetchrow("select * from prompt_settings where key='default'")
            original_settings = dict(saved) if saved else {}
        await registry.select("m4.0", actor="m8-verify")
        await registry.select("m8.0", actor="m8-verify")
        rolled = await registry.rollback(actor="m8-verify")
        assert rolled["selected_version"] == "m4.0"
        assert get_prompt_snapshot("m4.0").content_hash
        checks += 1
        print(f"M8 evaluation acceptance: PASS ({checks} checks)")
        return 0
    except Exception as exc:
        print(f"M8 evaluation acceptance: FAIL {type(exc).__name__}: {exc}")
        return 1
    finally:
        if not no_cleanup and run_id and dataset_id:
            try:
                async with database.connection() as conn, conn.transaction():
                    await conn.execute("delete from evaluation_runs where id=$1", run_id)
                    await conn.execute("delete from evaluation_datasets where id=$1", dataset_id)
                    if original_settings:
                        await conn.execute(
                            "update prompt_settings set selected_version=$1,previous_version=$2,"
                            "changed_by=$3,changed_at=$4 where key='default'",
                            original_settings["selected_version"],
                            original_settings["previous_version"],
                            original_settings["changed_by"],
                            original_settings["changed_at"],
                        )
                    elif original_settings is not None:
                        await conn.execute("delete from prompt_settings where key='default'")
            except Exception as exc:
                print(f"cleanup failed: {type(exc).__name__}: {exc}")
        await database.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.no_cleanup)))
