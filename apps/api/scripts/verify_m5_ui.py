"""M5 browser acceptance plus real Postgres concurrent source-snapshot regression.

Install the optional browser driver into the API venv with:
  uv pip install --python apps/api/.venv/Scripts/python.exe playwright
Use the installed Chrome/Edge; no Playwright browser download is needed.
Run Next dev on an unused port, then:
  node scripts/verify-m5-ui.mjs --web-url http://127.0.0.1:3107
All browser API traffic is intercepted. Postgres rows use random IDs and are cleaned up.
--skip-postgres runs only the browser; --postgres-only runs only the SQL regression.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.db import Database
from app.pdf import find_chromium
from app.repositories import JobDescriptionRepository, ResumeRepository
from app.schemas import JobProfile
from app.schemas.scoring import ScoreResponse

SOURCE_ID = "a5555555-5555-4555-8555-555555555555"
BEST_ID = "b5555555-5555-4555-8555-555555555555"
RUN_ID = "c5555555-5555-4555-8555-555555555555"
EXPERIENCE_ID = "d5555555-5555-4555-8555-555555555555"
JD_ID = "e5555555-5555-4555-8555-555555555555"
USER_ID = "00000000-0000-4000-8000-000000000001"
GENERATION_ID = "f5555555-5555-4555-8555-555555555555"
NOW = datetime.now(UTC).isoformat()
PROFILE = JobProfile(
    title="后端工程师",
    required_skills=["Python"],
    nice_to_have=[],
    keywords=["接口"],
    implicit_preferences=[],
    responsibilities=["接口开发"],
).model_dump(mode="json")


def sections(text):
    return [
        {
            "title": "项目经历",
            "entries": [
                {
                    "experience_id": EXPERIENCE_ID,
                    "kind": "project",
                    "org": "验收隔离项目",
                    "role": "开发者",
                    "period": "2026.09 – 至今",
                    "bullets": [{"text": text, "evidence": ["使用 Python 维护接口"]}],
                }
            ],
        }
    ]


def resume():
    return {
        "id": SOURCE_ID,
        "user_id": USER_ID,
        "jd_id": JD_ID,
        "title": "隔离验收简历",
        "template": "classic",
        "header": {"name": "验收用户", "headline": "后端开发"},
        "sections": sections("初稿要点：维护 Python 接口"),
        "status": "draft",
        "generator": "stub:ui-fixture",
        "generator_vendor": "stub",
        "created_at": NOW,
        "updated_at": NOW,
    }


def score_response(mode):
    scores = [60, 86, 77] if mode == "improved" else [88] if mode == "score_only" else [88, 70, 65]
    snapshots = []
    for round_, score in enumerate(scores):
        snapshots.append(
            {
                "round": round_,
                "sections": sections(
                    "初稿要点：维护 Python 接口"
                    if round_ == 0
                    else f"第{round_}轮要点：改写 Python 接口"
                ),
                "cost": float(round_ + 1),
                "result": {
                    "score": score,
                    "dimensions": [
                        {
                            "key": key,
                            "label": label,
                            "score": score,
                            "weight": 0.25,
                            "weighted_score": score * 0.25,
                            "rationale": f"第{round_}轮维度解释",
                        }
                        for key, label in [
                            ("relevance", "岗位相关性"),
                            ("coverage", "技能覆盖"),
                            ("evidence", "事实依据"),
                            ("clarity", "表达清晰"),
                        ]
                    ],
                    "deductions": [
                        {
                            "item_index": 0,
                            "bullet_index": 0,
                            "dimension": "clarity",
                            "points": 5,
                            "reason": f"第{round_}轮扣分原因",
                            "suggestion": "补充表达细节",
                        }
                    ],
                    "recommendations": [f"第{round_}轮改进建议"],
                    "low_score_items": [0],
                    "factual_violations": [],
                    "judge_provider": "stub",
                    "judge_model": "ui-fixture",
                    "blind": True,
                    "item_scores": {"0": score},
                    "is_stub": True,
                    "usage_tokens": 0,
                    "warnings": [],
                },
            }
        )
    best = snapshots[1] if mode == "improved" else snapshots[0]
    best_resume = {**resume(), "id": BEST_ID, "sections": best["sections"]}
    return ScoreResponse.model_validate(
        {
            "resume_id": SOURCE_ID,
            "profile": PROFILE,
            "loop": {"best": best, "snapshots": snapshots, "stop_reason": "max_rounds"},
            "resume": best_resume,
            "score_run_id": RUN_ID,
            "preview_path": f"/resumes/{BEST_ID}/html",
            "pdf_path": f"/resumes/{BEST_ID}/pdf",
        }
    ).model_dump(mode="json")


async def verify_postgres():
    settings = Settings(
        llm_provider="stub",
        judge_provider="stub",
        langfuse_public_key=None,
        langfuse_secret_key=None,
    )
    database = Database(settings)
    user = UUID(settings.dev_user_id)
    jobs = JobDescriptionRepository(database)
    repo = ResumeRepository(database)
    source_id, old_jd, new_jd = uuid4(), uuid4(), uuid4()
    resume_ids = [source_id]

    class PausedDatabase:
        """Hold the scoring write while another connection edits its captured source."""

        started = asyncio.Event()
        proceed = asyncio.Event()

        @asynccontextmanager
        async def connection(self):
            self.started.set()
            await self.proceed.wait()
            async with database.connection() as conn:
                yield conn

    task = None
    paused = PausedDatabase()
    try:
        for jd_id in (old_jd, new_jd):
            await jobs.create(
                user,
                jd_id=jd_id,
                raw_text="M5 隔离回归招聘 Python 后端开发工程师",
                title="M5 snapshot acceptance",
                company=None,
                parsed=PROFILE,
                parser_model="acceptance-fixture",
            )
        captured = await repo.create(
            user,
            resume_id=source_id,
            jd_id=old_jd,
            title="评分启动时标题",
            template="classic",
            header={"name": "评分启动时姓名", "headline": "评分启动时简介"},
            sections=resume()["sections"],
            generator="stub:captured",
            generator_vendor="stub",
        )
        optimized = sections("已优化的历史要点")
        result = {"loop": score_response("improved")["loop"], "profile": PROFILE}
        task = asyncio.create_task(
            ResumeRepository(paused).save_scoring_result(user, captured, optimized, result)
        )
        await paused.started.wait()
        async with database.connection() as conn:
            await conn.execute(
                "update public.resumes set title=$2,header=$3,jd_id=$4,template=$5,"
                "generator=$6,generator_vendor=$7,edit_revision=edit_revision+1 where id=$1",
                source_id,
                "评分期间新标题",
                {"name": "评分期间新姓名", "headline": "新简介"},
                new_jd,
                "modern",
                "manual",
                None,
            )
        paused.proceed.set()
        best, run_id = await task
        resume_ids.append(best["id"])
        for key in ("title", "header", "jd_id", "template", "generator", "generator_vendor"):
            assert best[key] == captured[key], f"captured {key} was replaced by live source"
        assert best["sections"] == optimized
        live = await repo.get(user, source_id)
        assert live["title"] == "评分期间新标题" and live["jd_id"] == new_jd
        history = await repo.get_scoring_result(user, source_id, run_id)
        snapshot = history["result"]["resume_snapshot"]
        assert snapshot["title"] == captured["title"]
        assert snapshot["header"] == captured["header"]
        assert snapshot["jd_id"] == str(old_jd)
        assert snapshot["template"] == captured["template"]
        assert snapshot["sections"] == optimized
        print("PASS Postgres: concurrent source edits preserve all captured metadata and history")
    finally:
        paused.proceed.set()
        if task is not None and not task.done():
            try:
                best, _ = await task
                resume_ids.append(best["id"])
            except Exception:
                pass
        async with database.connection() as conn:
            await conn.execute("delete from public.resumes where id=any($1::uuid[])", resume_ids)
            await conn.execute(
                "delete from public.job_descriptions where id=any($1::uuid[])", [old_jd, new_jd]
            )
        await database.close()


async def verify_browser(web_url, screenshot_dir):
    try:
        from playwright.async_api import async_playwright, expect
    except ImportError as exc:
        raise RuntimeError("Install playwright into apps/api/.venv (see script docstring)") from exc
    executable = find_chromium()
    assert executable, "Chrome/Edge is required; set RESUME_CHROMIUM to its executable"
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"PASS {label}", flush=True)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=executable, headless=True)
        try:
            for mode in ("improved", "score_only", "initial_best"):
                context = await browser.new_context(viewport={"width": 1440, "height": 1100})
                requests, unknown, errors = [], [], []
                score_calls = 0
                value = score_response(mode)
                score_release = asyncio.Event()
                generated = {
                    "run_id": GENERATION_ID,
                    "resume": resume(),
                    "items": [
                        {
                            "index": 0,
                            "experience_id": EXPERIENCE_ID,
                            "org": "验收隔离项目",
                            "status": "succeeded",
                            "entry": None,
                        }
                    ],
                    "failures": [
                        {
                            "index": 1,
                            "experience_id": JD_ID,
                            "error": "隔离失败条目",
                            "details": [],
                            "retryable": True,
                        }
                    ],
                    "profile": PROFILE,
                    "checkpoint": {
                        "status": "partial",
                        "updated_at": NOW,
                        "backend": "memory",
                        "resumable": True,
                    },
                    "preview_path": f"/resumes/{SOURCE_ID}/html",
                    "pdf_path": f"/resumes/{SOURCE_ID}/pdf",
                    "provider": "stub",
                    "model": "ui-fixture",
                    "is_stub": True,
                    "trace_id": "ui-acceptance",
                    "warnings": [],
                }

                async def intercept(
                    route,
                    request_arg=None,
                    request_log=requests,
                    generated=generated,
                    mode=mode,
                    score_release=score_release,
                    value=value,
                    unknown=unknown,
                ):
                    nonlocal score_calls
                    request = route.request
                    path = request.url.split("/api/v1", 1)[1].split("?", 1)[0]
                    method = request.method
                    if method == "OPTIONS":
                        await route.fulfill(
                            status=204,
                            headers={
                                "Access-Control-Allow-Origin": "*",
                                "Access-Control-Allow-Headers": "*",
                                "Access-Control-Allow-Methods": "*",
                            },
                        )
                        return
                    payload = request.post_data_json if request.post_data else None
                    request_log.append((method, path, payload))
                    status, response = 200, None
                    if path == "/experiences":
                        response = {
                            "items": [
                                {
                                    "id": EXPERIENCE_ID,
                                    "org": "验收隔离项目",
                                    "role": "开发者",
                                    "kind": "project",
                                    "skill_tags": ["Python"],
                                }
                            ]
                        }
                    elif path == f"/resumes/runs/{GENERATION_ID}":
                        response = generated
                    elif path == f"/resumes/{SOURCE_ID}/html":
                        await route.fulfill(
                            status=200, content_type="text/html", body="<p>验收预览</p>"
                        )
                        return
                    elif path == f"/resumes/{SOURCE_ID}/score" and method == "POST":
                        score_calls += 1
                        if mode == "improved" and score_calls == 1:
                            status, response = 502, {"detail": "隔离验收：评分服务暂时不可用"}
                        else:
                            await score_release.wait()
                            response = value
                    elif path == f"/resumes/{SOURCE_ID}/scores/{RUN_ID}" and method == "GET":
                        response = value
                    else:
                        unknown.append((method, path))
                        await route.abort()
                        return
                    await route.fulfill(
                        status=status,
                        content_type="application/json",
                        body=json.dumps(response, ensure_ascii=False),
                    )

                # Broad interception ensures fixture acceptance never reaches production API data.
                await context.route("**/api/v1/**", intercept)
                saved = json.dumps(
                    {"runId": GENERATION_ID, "jdId": None, "experienceIds": [EXPERIENCE_ID]}
                )
                await context.add_init_script(
                    "localStorage.setItem('resume-optimizer:last-generation-run', "
                    + json.dumps(saved)
                    + ");"
                )
                page = await context.new_page()
                page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                page.on("dialog", lambda dialog: dialog.accept())
                await page.goto(f"{web_url}/generate")
                panel = page.locator("section").filter(
                    has=page.get_by_role("heading", name="评分并优化", exact=True)
                )
                button = panel.get_by_role("button", name="评分并优化", exact=True)
                await expect(button).to_be_enabled(timeout=60000)
                check(await panel.is_visible(), f"generate/{mode}: scoring entry visible")
                await page.get_by_placeholder(
                    "把招聘网站上的 JD 直接粘进来即可，不用整理格式。"
                ).fill("招聘 Python 后端工程师，负责接口开发。")
                generate_button = page.get_by_role("button", name="生成简历", exact=True)
                retry_button = page.get_by_role("button", name="重试此条", exact=True)
                resume_button = page.get_by_role("button", name="继续生成未完成条目", exact=True)
                await expect(generate_button).to_be_enabled()
                await expect(retry_button).to_be_enabled()
                await expect(resume_button).to_be_enabled()
                await panel.get_by_label("目标分数", exact=True).fill("92")
                await panel.get_by_label("最多改写", exact=True).select_option(
                    "0" if mode == "score_only" else "2"
                )
                if mode == "improved":
                    await button.click()
                    await expect(panel.get_by_role("alert")).to_contain_text("评分服务暂时不可用")
                    await expect(button).to_be_enabled()
                    await expect(panel.get_by_label("目标分数")).to_be_enabled()
                    check(score_calls == 1, "score failure releases controls for retry")
                await button.click()
                await expect(
                    panel.get_by_role("button", name="评分与优化中…", exact=True)
                ).to_be_disabled()
                await expect(generate_button).to_be_disabled()
                await expect(retry_button).to_be_disabled()
                await expect(resume_button).to_be_disabled()
                check(True, f"{mode}: scoring locks generation, targeted retry and recovery")
                score_release.set()
                best_link = panel.get_by_role("link", name="打开最佳版编辑器")
                await expect(best_link).to_be_visible()
                await expect(generate_button).to_be_enabled()
                await expect(retry_button).to_be_enabled()
                await expect(resume_button).to_be_enabled()
                check(True, f"{mode}: completed scoring releases generation controls")
                actual_posts = [
                    body
                    for method, path, body in requests
                    if method == "POST" and path.endswith("/score")
                ]
                expected_payload = {
                    "threshold": 92,
                    "max_rounds": 0 if mode == "score_only" else 2,
                    "cost_limit": 100,
                    "persist": True,
                }
                check(
                    all(body == expected_payload for body in actual_posts),
                    f"generate/{mode}: score request carries controls and persistence",
                )
                check(
                    await best_link.get_attribute("href") == f"/edit/{BEST_ID}",
                    f"generate/{mode}: best editor points to saved best ID",
                )
                pdf_url = await panel.get_by_role("link", name="下载最佳版当前 PDF").get_attribute(
                    "href"
                )
                check(
                    f"/resumes/{BEST_ID}/pdf" in pdf_url,
                    f"generate/{mode}: PDF points to saved best ID",
                )
                await expect(panel.get_by_text("当前使用演示评分规则", exact=False)).to_be_visible()
                await expect(
                    panel.get_by_text("当前最佳稿来自演示生成器", exact=False)
                ).to_be_visible()
                check(True, f"generate/{mode}: judge and generator stub labels visible")
                round_buttons = panel.get_by_role("group", name="评分轮次").get_by_role("button")
                check(
                    await round_buttons.count() == len(value["loop"]["snapshots"]),
                    f"generate/{mode}: all score rounds visible",
                )
                for snapshot in value["loop"]["snapshots"]:
                    round_ = snapshot["round"]
                    label = "初稿" if round_ == 0 else f"第 {round_} 轮改写"
                    round_button = round_buttons.nth(round_)
                    await expect(round_button).to_contain_text(
                        f"{snapshot['result']['score']:.1f} 分"
                    )
                    await round_button.click()
                    await expect(round_button).to_have_attribute("aria-pressed", "true")
                    await expect(
                        panel.get_by_role("heading", name=f"初稿与{label}对比", exact=False)
                    ).to_be_visible()
                    await expect(
                        panel.get_by_text(f"第{round_}轮改进建议", exact=True)
                    ).to_be_visible()
                    await expect(
                        panel.get_by_text(f"第{round_}轮维度解释", exact=True)
                    ).to_have_count(4)
                    if await panel.locator("details").get_attribute("open") is None:
                        await panel.get_by_text("扣分原因与改进建议", exact=False).click()
                    await expect(
                        panel.get_by_text(f"第{round_}轮扣分原因", exact=False)
                    ).to_be_visible()
                    if round_:
                        await expect(
                            panel.get_by_text(f"第{round_}轮要点：改写 Python 接口", exact=True)
                        ).to_be_visible()
                    await expect(
                        panel.get_by_text("初稿要点：维护 Python 接口", exact=True).first
                    ).to_be_visible()
                check(
                    True,
                    f"generate/{mode}: per-round dimensions, deductions and text comparison",
                )
                if mode == "score_only":
                    await expect(panel.get_by_text("本次未发生改写", exact=False)).to_be_visible()
                elif mode == "initial_best":
                    await expect(
                        panel.get_by_text("最佳版保留初稿内容", exact=False)
                    ).to_be_visible()
                    await expect(round_buttons.first).to_contain_text("最佳")
                else:
                    await expect(round_buttons.nth(1)).to_contain_text("最佳")
                check(True, f"generate/{mode}: best and no-improvement explanations accurate")
                if screenshot_dir:
                    screenshot_dir.mkdir(parents=True, exist_ok=True)
                    await page.screenshot(
                        path=str(screenshot_dir / f"m5-generate-{mode}.png"), full_page=True
                    )
                posts_before_reload = len(actual_posts)
                await page.reload()
                await expect(page.get_by_role("link", name="打开最佳版编辑器")).to_be_visible()
                history_reads = [
                    path for method, path, _ in requests if method == "GET" and "/scores/" in path
                ]
                check(
                    history_reads
                    and all(
                        path == f"/resumes/{SOURCE_ID}/scores/{RUN_ID}" for path in history_reads
                    ),
                    f"generate/{mode}: refresh history GET uses source ID",
                )
                check(
                    sum(
                        method == "POST" and path.endswith("/score") for method, path, _ in requests
                    )
                    == posts_before_reload,
                    f"generate/{mode}: refresh recovers history without re-scoring",
                )
                check(not unknown, f"generate/{mode}: all API requests intercepted ({unknown})")
                check(not errors, f"generate/{mode}: no browser runtime errors ({errors})")
                await context.close()
        finally:
            await browser.close()
    print(f"M5 browser: {checks} checks passed; no model calls or production API writes")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web-url", default="http://127.0.0.1:3107")
    parser.add_argument("--skip-postgres", action="store_true")
    parser.add_argument("--postgres-only", action="store_true")
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    # Playwright needs the Windows subprocess-capable Proactor event loop. The SQL
    # regression uses only asyncpg, so neither path needs psycopg's Selector loop.
    if not args.skip_postgres:
        asyncio.run(verify_postgres())
    if not args.postgres_only:
        asyncio.run(verify_browser(args.web_url.rstrip("/"), args.screenshots))


if __name__ == "__main__":
    main()
