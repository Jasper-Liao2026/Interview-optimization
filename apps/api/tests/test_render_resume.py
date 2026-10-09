"""简历 HTML 渲染（M1-5）。

这一层是**预览与 PDF 的同一份来源**，所以它同时守着两件事：
  1. 安全性：用户录入的经历文本会原样进模板，必须被转义（否则就是一处 XSS）
  2. 分页能力：模板必须带着 `@page` 与 `break-inside` —— M1-7 的中文分页验收全靠它们
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.render import (
    DEFAULT_TEMPLATE,
    UnknownTemplateError,
    available_templates,
    render_resume_html,
)
from app.schemas import (
    ResumeBullet,
    ResumeEntry,
    ResumeHeader,
    ResumeRead,
    ResumeSection,
)


def _resume(*, org: str = "简历优化器", name: str = "本地开发用户") -> ResumeRead:
    now = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    return ResumeRead(
        id=uuid4(),
        user_id=uuid4(),
        jd_id=None,
        title="后端开发工程师（校招）",
        template=DEFAULT_TEMPLATE,
        header=ResumeHeader(name=name, headline="后端 / AI 应用开发"),
        sections=[
            ResumeSection(
                title="项目经历",
                entries=[
                    ResumeEntry(
                        experience_id=uuid4(),
                        kind="project",
                        org=org,
                        role="独立开发",
                        period="2026.09 – 至今",
                        bullets=[
                            ResumeBullet(
                                text="把请求 trace_id 复用为 Langfuse trace_id", evidence=[]
                            )
                        ],
                    )
                ],
            )
        ],
        status="draft",
        generator="pytest",
        created_at=now,
        updated_at=now,
    )


def test_available_templates_contains_the_default() -> None:
    assert DEFAULT_TEMPLATE in available_templates()


def test_render_includes_header_section_and_bullet() -> None:
    html = render_resume_html(_resume())

    assert html.startswith("<!DOCTYPE html>")
    assert "本地开发用户" in html
    assert "后端 / AI 应用开发" in html
    assert "项目经历" in html
    assert "把请求 trace_id 复用为 Langfuse trace_id" in html


def test_render_escapes_user_supplied_text() -> None:
    """经历里出现 `<script>` 时必须变成实体 —— 它就是一段用户输入。"""
    html = render_resume_html(_resume(org="<script>alert(1)</script>"))

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_render_escapes_quotes_in_org() -> None:
    html = render_resume_html(_resume(org='"><img onerror=alert(1)>'))
    assert "<img onerror" not in html
    assert "&#34;&gt;&lt;img" in html


def test_render_keeps_print_rules() -> None:
    """`@page` 与 `break-inside` 是中文分页的根据，被误删会静默破坏导出排版。"""
    html = render_resume_html(_resume())

    assert "@page" in html
    assert "break-inside" in html
    assert "@media print" in html


def test_render_declares_a_cjk_font_stack() -> None:
    """字体栈必须含中文字体，否则浏览器回退到无中文字形的字体就是一片方框。"""
    html = render_resume_html(_resume())
    assert "Microsoft YaHei" in html


def test_render_unknown_template_raises() -> None:
    with pytest.raises(UnknownTemplateError, match="未知模板"):
        render_resume_html(_resume(), template="does-not-exist")
