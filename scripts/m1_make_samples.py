"""M1-6 三方案对比：样例生成。

三份样例**必须吃同一份输入**，对比才成立 —— 否则比的是内容差异而不是方案差异。
本脚本负责：

  1. 复用 `m1_paginate.build_long_resume()` 造一份必然跨页的简历
  2. 落 `resume_sample.json`（方案 A 的 Node 脚本读它）与 `sample_input.html`
  3. **方案 B**：服务端无头 Chromium 打印 → `sample_B_server_chromium.pdf`
  4. 把同一份简历写进数据库拿到 id → 交给 Chrome 打印 `/print/<id>`
     （**方案 C 的真实路径**：Next.js 打印页 → iframe srcDoc → 浏览器原生打印）
  5. **方案 A** 由 `_pdfproto/render.mjs` 读同一份 JSON 产出（另一套排版引擎，刻意不在这里做）

必须在 `apps/api` 目录下运行（要 import app，也要读 apps/api/.env 里的库连接）。
用法：
    python scripts/m1_make_samples.py            # 只做 1~4 的服务端部分
    python scripts/m1_make_samples.py --print-c  # 额外跑方案 C（需要 web:3000 已起）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import UUID

# 允许直接以脚本路径运行：把 scripts 目录塞进 sys.path 才能 import 同级的 m1_paginate
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import get_settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.pdf import PdfExporter, find_chromium, pdf_page_count  # noqa: E402
from app.render import render_resume_html  # noqa: E402
from app.repositories import ResumeRepository  # noqa: E402

from m1_paginate import build_long_resume  # noqa: E402

OUT = Path(__file__).resolve().parent / "_m1_out"
OUT.mkdir(exist_ok=True)

PRINT_BASE = "http://127.0.0.1:3000"


def dump_inputs() -> dict:
    """写出三方案共用的输入，返回落盘信息。"""
    resume = build_long_resume()
    payload = resume.model_dump(mode="json")
    (OUT / "resume_sample.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    html = render_resume_html(resume, template=resume.template)
    (OUT / "sample_input.html").write_text(html, encoding="utf-8")

    return {
        "resume": resume,
        "payload": payload,
        "html": html,
        "entries": sum(len(s.entries) for s in resume.sections),
        "bullets": sum(len(e.bullets) for s in resume.sections for e in s.entries),
    }


async def export_plan_b(html: str) -> dict:
    exporter = PdfExporter(get_settings())
    if not exporter.available:
        return {"ok": False, "error": "未找到 Chromium 系浏览器"}
    pdf = await exporter.render(html)
    (OUT / "sample_B_server_chromium.pdf").write_bytes(pdf)
    return {
        "ok": True,
        "browser": exporter.browser_path,
        "bytes": len(pdf),
        "pages": pdf_page_count(pdf),
    }


async def persist_sample(resume) -> str:
    """把样例简历写进库，返回 id。

    为什么要落库：方案 C 走的是前端 `/print/<id>`，它通过 API 取 HTML。
    不落库就只能自己拼一个假页面，那就不是「真实路径」了。
    """
    settings = get_settings()
    database = Database(settings)
    try:
        repo = ResumeRepository(database)
        row = await repo.create(
            # 必须用 seed 里那个真实存在的开发用户，否则外键直接拒。
            # 样例简历是内存里造的，它的 user_id 是 uuid4()，库里没有对应 profiles 行。
            UUID(settings.dev_user_id),
            jd_id=None,
            title=resume.title,
            template=resume.template,
            header=resume.header.model_dump(mode="json"),
            sections=[s.model_dump(mode="json") for s in resume.sections],
            generator="m1-6-sample",
        )
        return str(row["id"])
    finally:
        await database.close()


def print_plan_c(resume_id: str) -> dict:
    """方案 C：用浏览器二进制打印真实的 `/print/<id>` 页面。

    与方案 B 的关键差别**不在渲染内核**（同一个 Blink），而在：
      - B 由服务端在临时目录里打印一份 HTML 文件
      - C 由浏览器加载一个真实页面、跑完前端 JS（取 HTML → 塞进 iframe → 撑高）
        再触发打印

    所以这一步同时验证了「Next.js 打印页真的能出纸」，而不只是「有一个 HTML」。
    """
    browser = find_chromium(get_settings().chromium_path)
    if browser is None:
        return {"ok": False, "error": "未找到 Chromium 系浏览器"}

    url = f"{PRINT_BASE}/print/{resume_id}"
    target = OUT / "sample_C_browser_print.pdf"

    with tempfile.TemporaryDirectory(prefix="resume-c-") as tmp:
        profile = Path(tmp) / "profile"
        profile.mkdir()
        command = [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--hide-scrollbars",
            f"--user-data-dir={profile}",
            # 前端要等一次 fetch（取 HTML）再设 srcDoc，虚拟时间预算给足；
            # 太短会打出「读取简历…」那一屏。
            "--virtual-time-budget=20000",
            "--run-all-compositor-stages-before-draw",
            f"--print-to-pdf={target}",
            url,
        ]
        completed = subprocess.run(  # noqa: S603
            command, capture_output=True, timeout=120, check=False
        )

    if not target.is_file():
        return {
            "ok": False,
            "error": "浏览器未产出 PDF",
            "exit": completed.returncode,
            "stderr": completed.stderr.decode("utf-8", "ignore")[-400:],
        }

    data = target.read_bytes()
    ok = data.startswith(b"%PDF")
    return {
        "ok": ok,
        "url": url,
        "bytes": len(data),
        "pages": pdf_page_count(data),
        "note": "若不 ok，多半是前端未启动或虚拟时间不足，PDF 里只剩工具栏文案",
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print-c", action="store_true", help="额外执行方案 C 的浏览器打印")
    args = parser.parse_args()

    info = dump_inputs()
    print(
        f"[input] entries={info['entries']} bullets={info['bullets']} "
        f"html_chars={len(info['html'])}",
        flush=True,
    )

    plan_b = await export_plan_b(info["html"])
    print(
        f"[B] ok={plan_b.get('ok')} pages={plan_b.get('pages')} "
        f"bytes={plan_b.get('bytes')} browser={Path(plan_b.get('browser') or '').name}",
        flush=True,
    )

    resume_id = await persist_sample(info["resume"])
    print(f"[db] resume_id={resume_id}", flush=True)

    plan_c: dict = {"ok": False, "skipped": True}
    if args.print_c:
        plan_c = print_plan_c(resume_id)
        print(
            f"[C] ok={plan_c.get('ok')} pages={plan_c.get('pages')} "
            f"bytes={plan_c.get('bytes')}",
            flush=True,
        )
        if not plan_c.get("ok"):
            print(f"     {plan_c.get('error')}", flush=True)

    report = {
        "resume_id": resume_id,
        "input": {"entries": info["entries"], "bullets": info["bullets"]},
        "plan_b": plan_b,
        "plan_c": plan_c,
    }
    (OUT / "samples_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
