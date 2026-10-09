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

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.schemas import ResumeRead

_TEMPLATE_DIR = Path(__file__).parent / "templates"

# 模板注册表：M7-1 会把它换成「按数据模型解耦的模板抽象层」，M1 先按名字直取
TEMPLATE_FILES: dict[str, str] = {
    "classic": "resume_classic.html.j2",
}
DEFAULT_TEMPLATE = "classic"

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


def render_resume_html(resume: ResumeRead, *, template: str = DEFAULT_TEMPLATE) -> str:
    """把一份简历渲染成完整 HTML 文档（含内联样式，可直接被浏览器打印）。"""
    filename = TEMPLATE_FILES.get(template)
    if filename is None:
        raise UnknownTemplateError(
            f"未知模板 {template!r}，可选：{', '.join(available_templates())}"
        )
    return _env.get_template(filename).render(resume=resume)
