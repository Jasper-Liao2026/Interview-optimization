"""简历 HTML 渲染（M1-5）。

**这一层的定位是「预览与 PDF 的同一份来源」**：
`GET /api/v1/resumes/{id}/html` 返回它给前端 iframe 预览，
无头浏览器打印的也是这一份。于是 M1-7 的验收标准「导出结果与预览一致」
不是一个需要靠人工反复比对的目标 —— 它是结构上的必然。

用了 `autoescape=True`（而不是 Jinja2 的 `select_autoescape(["html"])`）：
模板文件名是 `*.html.j2`，其扩展名是 `.j2` 而非 `.html`，
`select_autoescape` 按扩展名判断，会判定为「不需要转义」。用户录入的经历文本
直接进模板，必须转义，所以这里显式打开。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.schemas import ResumeRead

_TEMPLATE_DIR = Path(__file__).parent / "templates"


@dataclass(frozen=True)
class TemplateDefinition:
    id: str
    name: str
    description: str
    layout: str
    filename: str


TEMPLATES = (
    TemplateDefinition(
        "classic",
        "经典单栏",
        "简洁清晰的单栏排版，适合正式投递。",
        "single-column",
        "resume_classic.html.j2",
    ),
    TemplateDefinition(
        "modern",
        "现代双栏",
        "章节标题置于侧栏，正文保持阅读顺序。",
        "two-column",
        "resume_modern.html.j2",
    ),
)
# 保留 M1 调用方的注册表接口；新增模板只需注册一个定义。
TEMPLATE_FILES = {definition.id: definition.filename for definition in TEMPLATES}
DEFAULT_TEMPLATE = "classic"


@dataclass(frozen=True)
class DocumentHeader:
    name: str
    headline: str | None


@dataclass(frozen=True)
class DocumentBullet:
    text: str


@dataclass(frozen=True)
class DocumentEntry:
    org: str
    role: str
    period: str | None
    bullets: tuple[DocumentBullet, ...]


@dataclass(frozen=True)
class DocumentSection:
    title: str
    entries: tuple[DocumentEntry, ...]


@dataclass(frozen=True)
class ResumeDocument:
    """模板只读的展示数据，不携带数据库标识、事实证据或生成状态。"""

    title: str
    header: DocumentHeader
    sections: tuple[DocumentSection, ...]

    @classmethod
    def from_resume(cls, resume: ResumeRead) -> ResumeDocument:
        return cls(
            title=resume.title,
            header=DocumentHeader(resume.header.name, resume.header.headline),
            sections=tuple(
                DocumentSection(
                    title=section.title,
                    entries=tuple(
                        DocumentEntry(
                            org=entry.org,
                            role=entry.role,
                            period=entry.period,
                            bullets=tuple(DocumentBullet(bullet.text) for bullet in entry.bullets),
                        )
                        for entry in section.entries
                    ),
                )
                for section in resume.sections
            ),
        )


_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR, encoding="utf-8"),
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
)


class UnknownTemplateError(ValueError):
    """请求了未注册的模板名。"""


def available_templates() -> list[str]:
    return sorted(TEMPLATE_FILES)


def template_definitions() -> list[dict[str, str]]:
    """返回供导出界面展示的公开模板信息。"""
    return [
        {"id": item.id, "name": item.name, "description": item.description, "layout": item.layout}
        for item in TEMPLATES
    ]


def render_resume_html(resume: ResumeRead, *, template: str | None = None) -> str:
    """把一份简历渲染成完整 HTML 文档（含内联样式，可直接被浏览器打印）。"""
    template = resume.template if template is None else template
    filename = TEMPLATE_FILES.get(template)
    if filename is None:
        raise UnknownTemplateError(
            f"未知模板 {template!r}，可选：{', '.join(available_templates())}"
        )
    return _env.get_template(filename).render(resume=ResumeDocument.from_resume(resume))
