"""PDF 导出（方案 B：服务端无头 Chromium 打印）。

## 为什么走这条路

三种候选方案（详见 `docs/M1-pdf-export-comparison.md`）：

| 方案 | 内核 | 与预览一致性 |
|---|---|---|
| A · 前端 `@react-pdf/renderer` | 自有排版引擎 | **差**：要维护第二套布局代码 |
| B · 服务端无头 Chromium | 与浏览器同一渲染器 | **天然一致**：打印的就是预览那份 HTML |
| C · 浏览器原生 `window.print()` | 与 B 同一引擎 | 一致，但只能由用户手动触发 |

B 与 C 是同一个渲染器，差别只在**谁触发**：
C 适合「用户点一下、立即下载」，B 适合「服务端批量出、或拿到 Bytes 直接返回」。
本项目两个都要（批量直出是 M7-5 的核心场景），所以主路径选 B，C 作为前端零依赖的备用入口。

## 为什么不下载 Playwright

本机已装 Edge / Chrome，直接调 CLI 就够：
不必为服务器额外拉 ~150MB 的浏览器内核，部署体积与启动时间都省下来。
代价是要自己处理进程超时与临时目录 —— 全部收在本文件里。
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from app.config import Settings, get_settings

logger = logging.getLogger("app.pdf")

# Chrome 系浏览器打印的 PDF：正文页对象形如 `/Type /Page`（注意排除 `/Pages`）
_PAGE_OBJECT = re.compile(rb"/Type\s*/Page(?![s])")

# Windows 上常见的安装位置。顺序即优先级：Chrome 优先于 Edge（渲染行为更可控）
_WINDOWS_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)
_POSIX_CANDIDATES = (
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)


class PdfExportError(RuntimeError):
    """导出失败。router 会把它翻成 503 + 明确原因（而不是一个没有线索的 500）。"""


def find_chromium(configured: str | None = None) -> str | None:
    """找一个可用的 Chromium 系浏览器。找不到返回 None，由调用方降级。"""
    candidates: list[str] = []
    if configured:
        candidates.append(configured)
    for env_name in ("RESUME_CHROMIUM", "CHROME_PATH", "CHROMIUM_PATH"):
        value = os.environ.get(env_name)
        if value:
            candidates.append(value)
    candidates.extend(_WINDOWS_CANDIDATES if sys.platform == "win32" else _POSIX_CANDIDATES)
    # 最后再问 PATH（Linux 容器 / 已加到 PATH 的环境）
    for name in ("google-chrome", "chromium", "chromium-browser", "msedge"):
        found = shutil.which(name)
        if found:
            candidates.append(found)

    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def pdf_page_count(data: bytes) -> int | None:
    """粗略数一下 PDF 页数。用于「中文分页是否正常」的自动校验。"""
    count = len(_PAGE_OBJECT.findall(data))
    return count or None


class PdfExporter:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        # 探测一次就缓存：浏览器路径在一次进程生命周期里不会变
        self._browser = find_chromium(self._settings.chromium_path)

    @property
    def browser_path(self) -> str | None:
        return self._browser

    @property
    def available(self) -> bool:
        return self._browser is not None

    def _command(self, *, user_data_dir: Path, pdf_path: Path, html_path: Path) -> list[str]:
        assert self._browser is not None
        return [
            self._browser,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-extensions",
            "--hide-scrollbars",
            "--mute-audio",
            # 独立 profile：否则已开着的 Chrome 会复用主实例，headless 参数被忽略
            f"--user-data-dir={user_data_dir}",
            # 去掉页眉页脚的日期 / 文件路径（新老版本各有旗标，都传，未知的会被忽略）
            "--no-pdf-header-footer",
            "--print-to-pdf-no-header",
            # 等布局与字体就绪再打印，否则偶发打出空白页
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=10000",
            f"--print-to-pdf={pdf_path}",
            html_path.as_uri(),
        ]

    async def render(self, html: str, *, timeout_s: float | None = None) -> bytes:
        """把 HTML 渲染成 PDF 字节。

        临时目录建在**系统临时目录**而不是项目目录里：
        项目目录受沙箱保护，清理临时文件会被拦；而且临时产物本就不该出现在仓库里。
        """
        if self._browser is None:
            raise PdfExportError(
                "未找到可用的 Chromium 系浏览器（Chrome / Edge）。"
                "请安装其一，或用 CHROMIUM_PATH 指定可执行文件路径。"
            )

        timeout = timeout_s or self._settings.pdf_timeout_s

        with tempfile.TemporaryDirectory(prefix="resume-pdf-") as tmp:
            tmp_dir = Path(tmp)
            html_path = tmp_dir / "resume.html"
            pdf_path = tmp_dir / "resume.pdf"
            user_data_dir = tmp_dir / "profile"
            user_data_dir.mkdir()

            html_path.write_text(html, encoding="utf-8")

            command = self._command(
                user_data_dir=user_data_dir, pdf_path=pdf_path, html_path=html_path
            )
            if sys.platform == "win32":
                # psycopg 要求的 SelectorEventLoop 不支持异步子进程。在工作线程
                # 启动 Chromium，subprocess.run 会在超时后杀掉并等待子进程退出。
                try:
                    result = await asyncio.to_thread(
                        subprocess.run,
                        command,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        timeout=timeout,
                        check=False,
                    )
                except subprocess.TimeoutExpired as exc:
                    raise PdfExportError(f"无头浏览器打印超时（>{timeout:.0f}s）") from exc
                stderr, returncode = result.stderr, result.returncode
            else:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    _, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
                except TimeoutError as exc:
                    process.kill()
                    await process.wait()
                    raise PdfExportError(f"无头浏览器打印超时（>{timeout:.0f}s）") from exc
                returncode = process.returncode

            if not pdf_path.is_file():
                detail = (stderr or b"").decode("utf-8", "ignore").strip()[-500:]
                raise PdfExportError(
                    f"无头浏览器未产出 PDF（exit={returncode}）：{detail or '无 stderr 输出'}"
                )

            data = pdf_path.read_bytes()

        if not data.startswith(b"%PDF"):
            raise PdfExportError("产出文件不是合法 PDF（缺少 %PDF 头）")

        logger.info(
            "pdf rendered bytes=%d pages=%s browser=%s",
            len(data),
            pdf_page_count(data),
            Path(self._browser).name,
        )
        return data


_exporter: PdfExporter | None = None


def get_pdf_exporter() -> PdfExporter:
    global _exporter
    if _exporter is None:
        _exporter = PdfExporter(get_settings())
    return _exporter


def reset_pdf_exporter() -> None:
    """测试用：清掉缓存的探测器，让下一次重新探测。"""
    global _exporter
    _exporter = None
