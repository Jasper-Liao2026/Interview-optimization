"""简历渲染层。

`render_resume_html()` 是预览与 PDF 的共同来源；模板放在 `templates/` 下。
"""

from app.render.resume_html import (
    DEFAULT_TEMPLATE,
    TEMPLATE_FILES,
    UnknownTemplateError,
    available_templates,
    render_resume_html,
)

__all__ = [
    "DEFAULT_TEMPLATE",
    "TEMPLATE_FILES",
    "UnknownTemplateError",
    "available_templates",
    "render_resume_html",
]
