"""M1-7 中文分页专项验证。

做法：
  1. 用**真实的模板**渲染一份**必然跨页**的简历（把要点铺开到 3 页以上）
  2. 交给服务端导出器打印成 PDF
  3. 交给 `m1_pdf_probe.py`（用 PyMuPDF）逐页抽取文本，断言：
       - 页数 ≥ 2（确实跨页了，否则这个测试没意义）
       - 每条要点完整落在**同一页**内（没被分页切断）
       - 每段经历的 org / role / 时间 / 要点**同页**（没被拆开）
       - 中文可被抽取 → 说明字体已嵌入且带 ToUnicode 映射（不是画成图形的方框）

必须在 apps/api 目录下运行（要 import app）。
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

from app.config import get_settings
from app.pdf import PdfExporter, pdf_page_count
from app.render import render_resume_html
from app.schemas import (
    ResumeBullet,
    ResumeEntry,
    ResumeHeader,
    ResumeRead,
    ResumeSection,
)

OUT = Path(__file__).resolve().parent / "_m1_out"
OUT.mkdir(exist_ok=True)

BULLETS = [
    "独立设计并实现简历优化器的后端服务，使用 FastAPI 承载全部业务逻辑，"
    "并以 LangGraph 编排「JD 解析 → 经历改写 → 评分回环」的多步流程，完成从 0 到 1 的工程化落地",
    "接入 Langfuse 构建可观测性体系：把每个 HTTP 请求的 trace_id 规范化为 32 位十六进制后"
    "复用为 Langfuse trace_id，使前端日志、后端日志与每一次 LLM 调用都能用同一个 ID 回放整条链路",
    "将数据库连接池改为惰性建立，应用启动阶段不再尝试连接 Postgres，"
    "避免数据库慢启动把 API 进程一起拖死，同时让 /health 在任何依赖不可用时都能返回 200",
    "以 Pydantic model 作为接口类型的唯一定义源，通过 OpenAPI 自动生成前端 TypeScript 类型，"
    "并用 CI 校验生成结果是否漂移，从结构上消除前后端类型双写带来的不一致",
    "为无钥匙环境实现确定性桩 provider：按 JSON Schema 生成形状合法、内容明确标注为假的占位数据，"
    "使观测链路、渲染链路与导出链路在没有真实模型时依然可以被端到端验证",
    "把简历的 HTML 预览与 PDF 导出收敛到同一个模板，"
    "导出时用无头 Chromium 打印这一份 HTML，从结构上保证「所见即所得」而不是靠人工比对维持",
]

ENTRIES = [
    ("实习经历", "南昌某网络科技有限公司", "后端开发实习生", "2026.03 – 2026.06", 5),
    ("项目经历", "简历优化器", "独立开发", "2026.09 – 至今", 6),
    ("项目经历", "分布式限流网关", "核心开发", "2026.05 – 2026.08", 4),
    ("校园经历", "东华理工大学计算机协会", "技术部成员", "2025.09 – 至今", 4),
]


def build_long_resume() -> ResumeRead:
    sections: list[ResumeSection] = []
    for section_title, org, role, period, bullet_count in ENTRIES:
        bullets = [
            ResumeBullet(text=BULLETS[index % len(BULLETS)], evidence=[])
            for index in range(bullet_count)
        ]
        entry = ResumeEntry(
            experience_id=uuid4(),
            kind={"实习经历": "internship", "项目经历": "project", "校园经历": "campus"}[section_title],
            org=org,
            role=role,
            period=period,
            bullets=bullets,
        )
        existing = next((item for item in sections if item.title == section_title), None)
        if existing is None:
            sections.append(ResumeSection(title=section_title, entries=[entry]))
        else:
            existing.entries.append(entry)

    now = datetime.now(UTC)
    return ResumeRead(
        id=uuid4(),
        user_id=uuid4(),
        jd_id=None,
        title="后端开发工程师（校招）",
        template="classic",
        header=ResumeHeader(name="本地开发用户", headline="后端 / AI 应用开发"),
        sections=sections,
        status="draft",
        generator="m1-7-pagination-probe",
        created_at=now,
        updated_at=now,
    )


async def main() -> int:
    resume = build_long_resume()
    html = render_resume_html(resume, template=resume.template)
    (OUT / "paginate.html").write_text(html, encoding="utf-8")

    exporter = PdfExporter(get_settings())
    report: dict = {"browser": exporter.browser_path, "html_chars": len(html)}
    if not exporter.available:
        report["ok"] = False
        report["error"] = "找不到 Chromium 系浏览器"
    else:
        pdf = await exporter.render(html)
        (OUT / "paginate.pdf").write_bytes(pdf)
        report["pdf_bytes"] = len(pdf)
        report["pages"] = pdf_page_count(pdf)
        # 期望的断言数据交给探针脚本核对
        report["expected_bullets"] = [
            bullet.text
            for section in resume.sections
            for entry in section.entries
            for bullet in entry.bullets
        ]
        report["expected_entries"] = [
            {"org": entry.org, "role": entry.role, "period": entry.period}
            for section in resume.sections
            for entry in section.entries
        ]
        report["ok"] = True

    (OUT / "paginate_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"ok": report.get("ok"), "pages": report.get("pages")}, ensure_ascii=False))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
