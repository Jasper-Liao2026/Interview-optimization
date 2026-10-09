"""PDF 逐页探针。

用 PyMuPDF 做三件在「分页/中文」验证里必须做的事：
  1. 逐页抽取文本 → 判断某段内容是否被分页切断
  2. 列出嵌入字体 → 判断中文是否真的被嵌入（而不是渲染成图形或方框）
  3. 首屏渲染成 PNG → 人眼可核对（本机没有 pdftoppm / magick）

PyMuPDF 不装进项目依赖：本脚本通过 `uv run --with pymupdf` 在**临时环境**里跑，
实验工具不该污染产品依赖。

用法：
    uv run --no-project --with pymupdf python scripts/m1_pdf_probe.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import fitz  # PyMuPDF

OUT = Path(__file__).resolve().parent / "_m1_out"


def normalize(text: str) -> str:
    """去掉全部空白。

    PyMuPDF 会在视觉换行处插入 \\n；而「一条要点是否被分页切断」关心的是
    字符是否连续，与换行无关，所以两边都做无空白归一化再比对。
    """
    return "".join(text.split())


def probe(pdf_path: Path, *, render_pages: int = 3) -> dict:
    doc = fitz.open(pdf_path)
    pages = [normalize(page.get_text()) for page in doc]

    fonts: set[str] = set()
    for page in doc:
        for font in page.get_fonts(full=True):
            # font[3] 是 BaseFont 名，font[4] 是字体类型（Type0 / TrueType / ...）
            fonts.add(f"{font[3]} ({font[4]})")

    images: list[str] = []
    for index in range(min(render_pages, len(pages))):
        pixmap = doc.load_page(index).get_pixmap(dpi=110)
        target = pdf_path.with_name(f"{pdf_path.stem}_p{index + 1}.png")
        pixmap.save(target)
        images.append(target.name)

    result = {
        "file": pdf_path.name,
        "bytes": pdf_path.stat().st_size,
        "pages": len(pages),
        "fonts": sorted(fonts),
        "page_images": images,
        "page_char_counts": [len(page) for page in pages],
    }
    doc.close()
    return result, pages


def check_pagination() -> dict:
    """M1-7 的核心断言：条目不跨页、要点不被切断。"""
    report = json.loads((OUT / "paginate_report.json").read_text(encoding="utf-8"))
    info, pages = probe(OUT / "paginate.pdf", render_pages=3)

    failures: list[str] = []

    if info["pages"] < 2:
        failures.append(f"只有 {info['pages']} 页，没有真正跨页，这个测试不成立")

    # 1. 每条要点必须完整落在同一页
    for bullet in report["expected_bullets"]:
        needle = normalize(bullet)
        if not any(needle in page for page in pages):
            failures.append(f"要点被分页切断或丢失：{bullet[:32]}…")

    # 2. 每段经历的抬头三要素（org/role/period）必须落在同一页。
    #    注意不要拼成 "org+role" 连续串再匹配：模板里两者之间有分隔符（如「·」），
    #    去空白归一化也去不掉它，会导致假失败。逐要素判定才是真正的语义。
    for entry in report["expected_entries"]:
        org = normalize(entry["org"])
        role = normalize(entry["role"])
        period = normalize(entry["period"] or "")
        head_page = next(
            (
                i
                for i, page in enumerate(pages)
                if org in page and role in page and (not period or period in page)
            ),
            None,
        )
        if head_page is None:
            failures.append(f"经历抬头被拆页或丢失：{entry['org']} · {entry['role']}")

    # 3. 中文字形可抽取 —— 说明字体已嵌入且带 ToUnicode 映射
    cjk_pages = sum(1 for page in pages if any("\u4e00" <= ch <= "\u9fff" for ch in page))
    if cjk_pages != info["pages"]:
        failures.append(f"有 {info['pages'] - cjk_pages} 页抽不到中文，可能是字体未嵌入或渲染成图形")

    info["failures"] = failures
    info["ok"] = not failures
    return info


def probe_samples(names: list[str]) -> list[dict]:
    results = []
    for name in names:
        path = OUT / name
        if not path.is_file():
            results.append({"file": name, "missing": True})
            continue
        info, pages = probe(path, render_pages=2)
        # 记录首屏是否含中文
        info["page1_has_cjk"] = any("\u4e00" <= ch <= "\u9fff" for ch in pages[0])
        results.append(info)
    return results


def main() -> int:
    summary: dict = {}

    pagination = check_pagination()
    summary["pagination"] = pagination
    print(f"[pagination] pages={pagination['pages']} ok={pagination['ok']}")
    for failure in pagination["failures"]:
        print(f"  ✗ {failure}")

    samples = probe_samples(
        [
            "sample_A_react_pdf.pdf",
            "sample_B_server_chromium.pdf",
            "sample_C_browser_print.pdf",
        ]
    )
    summary["samples"] = samples
    for info in samples:
        if info.get("missing"):
            print(f"[sample] {info['file']} 缺失")
        else:
            print(
                f"[sample] {info['file']} pages={info['pages']} "
                f"bytes={info['bytes']} cjk_p1={info['page1_has_cjk']}"
            )

    (OUT / "pdf_probe.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0 if pagination["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
