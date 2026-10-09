"""PDF 导出（M1-6）。

守三件事：
  1. 浏览器探测的降级行为（找不到浏览器时**必须**给出可读错误，而不是崩在 subprocess）
  2. 打印参数里几个「不写就会出问题」的旗标（页眉页脚、独立 profile、等布局就绪）
  3. 页数统计 —— 它是自动化校验分页的唯一手段
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.pdf import PdfExporter, PdfExportError, find_chromium, pdf_page_count


@pytest.fixture
def fake_browser(tmp_path: Path) -> Path:
    exe = tmp_path / "chrome.exe"
    exe.write_bytes(b"not a real browser")
    return exe


# -------------------------------------------------------------- find_chromium
def test_find_chromium_uses_configured_path(fake_browser: Path) -> None:
    assert find_chromium(str(fake_browser)) == str(fake_browser)


def test_find_chromium_reads_env_var(fake_browser: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESUME_CHROMIUM", str(fake_browser))
    assert find_chromium(None) == str(fake_browser)


def test_find_chromium_ignores_missing_configured_path(fake_browser: Path) -> None:
    """配置写错时不能把死路径原样返回 —— 否则 subprocess 会以一个难查的 OSError 炸掉。"""
    assert find_chromium("Z:/definitely/not/here/chrome.exe") != "Z:/definitely/not/here/chrome.exe"


# --------------------------------------------------------------- pdf_page_count
def test_pdf_page_count_counts_pages_but_not_the_page_tree() -> None:
    """正则要排除 `/Type /Pages`（页对象树），否则页数会多算一个。"""
    data = b"%PDF-1.4\n/Type /Page\n/Type /Pages\n/Type /Page\n%%EOF"
    assert pdf_page_count(data) == 2


def test_pdf_page_count_single_page() -> None:
    assert pdf_page_count(b"%PDF-1.4 /Type /Page /Type /Pages") == 1


def test_pdf_page_count_returns_none_when_absent() -> None:
    assert pdf_page_count(b"just some bytes") is None


# ------------------------------------------------------------------ PdfExporter
def test_command_disables_headers_and_uses_isolated_profile(
    fake_browser: Path, tmp_path: Path
) -> None:
    """这几个旗标是本方案与「浏览器原生打印」的实质差别，被删掉会静默改变产物。"""
    exporter = PdfExporter(Settings(chromium_path=str(fake_browser)))
    command = exporter._command(
        user_data_dir=tmp_path / "profile",
        pdf_path=tmp_path / "out.pdf",
        html_path=tmp_path / "in.html",
    )

    assert "--no-pdf-header-footer" in command
    assert "--print-to-pdf-no-header" in command
    # 独立 profile：否则已开着的 Chrome 会复用主实例，headless 参数被忽略
    assert any(arg.startswith("--user-data-dir=") for arg in command)
    # 等布局与字体就绪，否则偶发打出空白页
    assert "--run-all-compositor-stages-before-draw" in command
    assert command[-1].startswith("file://")


def test_exporter_reports_availability(fake_browser: Path) -> None:
    exporter = PdfExporter(Settings(chromium_path=str(fake_browser)))
    assert exporter.available is True
    assert exporter.browser_path == str(fake_browser)


async def test_render_without_browser_raises_readable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有浏览器是「能力缺失」，必须给出可行的补救办法，而不是一个裸 OSError。"""
    monkeypatch.setattr("app.pdf.export.find_chromium", lambda *args, **kwargs: None)

    exporter = PdfExporter(Settings())
    assert exporter.available is False

    with pytest.raises(PdfExportError, match="未找到可用的 Chromium"):
        await exporter.render("<html></html>")
