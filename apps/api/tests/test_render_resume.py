"""简历 HTML 渲染（M1-5）。

这一层是**预览与 PDF 的同一份来源**，所以它同时守着两件事：
  1. 安全性：用户录入的经历文本会原样进模板，必须被转义（否则就是一处 XSS）
  2. 分页能力：模板必须带着 `@page` 与 `break-inside` —— M1-7 的中文分页验收全靠它们
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.render import (
    DEFAULT_TEMPLATE,
    ResumeDocument,
    UnknownTemplateError,
    available_templates,
    render_resume_html,
    template_definitions,
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


@pytest.mark.parametrize("template", available_templates())
def test_render_includes_header_section_and_bullet(template: str) -> None:
    html = render_resume_html(_resume(), template=template)

    assert html.startswith("<!DOCTYPE html>")
    assert "本地开发用户" in html
    assert "后端 / AI 应用开发" in html
    assert "项目经历" in html
    assert "把请求 trace_id 复用为 Langfuse trace_id" in html
    assert f'data-template="{template}"' in html


@pytest.mark.parametrize("template", available_templates())
def test_render_escapes_user_supplied_text(template: str) -> None:
    """经历里出现 `<script>` 时必须变成实体 —— 它就是一段用户输入。"""
    html = render_resume_html(_resume(org="<script>alert(1)</script>"), template=template)

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


@pytest.mark.parametrize("template", available_templates())
def test_render_escapes_quotes_in_org(template: str) -> None:
    html = render_resume_html(_resume(org='"><img onerror=alert(1)>'), template=template)
    assert "<img onerror" not in html
    assert "&#34;&gt;&lt;img" in html


@pytest.mark.parametrize("template", available_templates())
def test_render_keeps_print_rules(template: str) -> None:
    """`@page` 与 `break-inside` 是中文分页的根据，被误删会静默破坏导出排版。"""
    html = render_resume_html(_resume(), template=template)

    assert "@page" in html
    assert "break-inside" in html
    assert "@media print" in html
    assert "overflow-wrap: anywhere" in html


@pytest.mark.parametrize("template", available_templates())
def test_render_declares_a_cjk_font_stack(template: str) -> None:
    """字体栈必须含中文字体，否则浏览器回退到无中文字形的字体就是一片方框。"""
    html = render_resume_html(_resume(), template=template)
    assert "Microsoft YaHei" in html


def test_render_unknown_template_raises() -> None:
    with pytest.raises(UnknownTemplateError, match="未知模板"):
        render_resume_html(_resume(), template="does-not-exist")


def test_default_uses_resume_template_and_explicit_override_does_not_mutate() -> None:
    resume = _resume()
    resume.template = "modern"
    before = resume.model_dump()
    assert 'data-template="modern"' in render_resume_html(resume)
    assert 'data-template="classic"' in render_resume_html(resume, template="classic")
    assert resume.model_dump() == before
    resume.template = "unknown-saved-template"
    with pytest.raises(UnknownTemplateError):
        render_resume_html(resume)


def test_template_metadata_is_public_and_returns_independent_values() -> None:
    definitions = template_definitions()
    assert {item["id"] for item in definitions} == set(available_templates())
    assert {item["layout"] for item in definitions} == {"single-column", "two-column"}
    assert all(set(item) == {"id", "name", "description", "layout"} for item in definitions)
    definitions[0]["name"] = "changed"
    assert template_definitions()[0]["name"] != "changed"


def test_document_is_detached_immutable_display_data() -> None:
    resume = _resume()
    resume.sections[0].entries[0].bullets[0].evidence = ["private original material"]
    document = ResumeDocument.from_resume(resume)
    data = asdict(document)
    assert set(data) == {"title", "header", "sections"}
    assert set(data["sections"][0]["entries"][0]) == {"org", "role", "period", "bullets"}
    assert data["sections"][0]["entries"][0]["bullets"] == (
        {"text": "把请求 trace_id 复用为 Langfuse trace_id"},
    )
    resume.header.name = "changed"
    resume.sections[0].entries[0].bullets[0].text = "changed"
    assert document.header.name == "本地开发用户"
    assert document.sections[0].entries[0].bullets[0].text != "changed"
    with pytest.raises(FrozenInstanceError):
        document.title = "changed"


@pytest.mark.parametrize("template", available_templates())
def test_templates_preserve_user_section_entry_order_and_escape_every_field(template: str) -> None:
    resume = _resume()
    payload = '<img src=x onerror="alert(1)">'
    resume.title = payload
    resume.header.name = payload
    resume.header.headline = payload
    first = resume.sections[0]
    first.title = "FIRST " + payload
    first.entries[0].role = payload
    first.entries[0].period = payload
    first.entries[0].bullets[0].text = payload
    second = first.model_copy(deep=True)
    second.title = "SECOND"
    second.entries[0].org = "SECOND ORG"
    resume.sections.append(second)
    html = render_resume_html(resume, template=template)
    assert "<img" not in html
    assert "&lt;img" in html
    assert html.index("FIRST") < html.index("SECOND") < html.index("SECOND ORG")
