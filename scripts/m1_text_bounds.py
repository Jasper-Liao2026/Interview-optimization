"""量 PDF 里文字的实际横向边界 vs 页面宽度。

用途：判断「要点文字是否溢出右边距」。目测截图不可靠，直接读 block 坐标。
用法：
    uv run --no-project --with pymupdf python scripts/m1_text_bounds.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import fitz

OUT = Path(__file__).resolve().parent / "_m1_out"
MM = 2.8346456693


def measure(name: str) -> dict:
    doc = fitz.open(OUT / name)
    page = doc.load_page(0)
    rect = page.rect
    right_margin_pt = 16 * MM
    content_right = rect.width - right_margin_pt

    blocks = [b for b in page.get_text("blocks") if b[4].strip()]
    max_x1 = max(b[2] for b in blocks) if blocks else 0
    overflow = max_x1 - content_right

    # 找溢出的那几条（右上角越界的文本）
    offenders = [
        {"text": b[4].strip()[:40], "x0": round(b[0], 1), "x1": round(b[2], 1)}
        for b in blocks
        if b[2] > content_right + 1
    ]

    doc.close()
    return {
        "file": name,
        "page_width_pt": round(rect.width, 1),
        "content_right_pt": round(content_right, 1),
        "max_text_x1_pt": round(max_x1, 1),
        "overflow_pt": round(overflow, 1),
        "violations": len(offenders),
        "sample": offenders[:3],
    }


def main() -> int:
    names = [
        "sample_A_react_pdf.pdf",
        "sample_B_server_chromium.pdf",
        "sample_C_browser_print.pdf",
    ]
    results = [measure(n) for n in names if (OUT / n).is_file()]
    for item in results:
        print(
            f"{item['file']}: page={item['page_width_pt']}pt "
            f"limit={item['content_right_pt']}pt max_x1={item['max_text_x1_pt']}pt "
            f"overflow={item['overflow_pt']}pt violations={item['violations']}"
        )
        for s in item["sample"]:
            print(f"    x1={s['x1']} {s['text']}")
    (OUT / "text_bounds.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
