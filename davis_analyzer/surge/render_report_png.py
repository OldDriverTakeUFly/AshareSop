#!/usr/bin/env python3
"""surge 报告 md → PNG 渲染(验收用,一次性工具).

md 表格宽,用 1400px viewport 2x 截图;>9000px 拆上下两段(长图先例)。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO = Path("/home/leo/Projects/CodeAgentDashboard")

_CSS = """
body { font-family: 'Noto Sans CJK SC', sans-serif; margin: 24px;
       font-size: 15px; line-height: 1.55; color: #1a1a1a; }
table { border-collapse: collapse; font-size: 12.5px; margin: 12px 0; }
th, td { border: 1px solid #bbb; padding: 4px 8px; white-space: nowrap; }
th { background: #f0f0f0; }
h1 { font-size: 22px; border-bottom: 2px solid #333; padding-bottom: 6px; }
h2 { font-size: 18px; margin-top: 20px; }
h3 { font-size: 15px; }
blockquote { color: #666; border-left: 3px solid #999; padding-left: 10px; }
"""


def _md_to_html(md_path: Path) -> str:
    import markdown
    return markdown.markdown(
        md_path.read_text(encoding="utf-8"),
        extensions=["tables", "fenced_code"])


async def render(md_path: Path, png_path: Path) -> None:
    from playwright.async_api import async_playwright
    html = f"<html><head><meta charset='utf-8'><style>{_CSS}</style></head>" \
           f"<body>{_md_to_html(md_path)}</body></html>"
    tmp = png_path.with_suffix(".tmp.html")
    tmp.write_text(html, encoding="utf-8")
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(
            viewport={"width": 1900, "height": 1200}, device_scale_factor=2)
        await page.goto(tmp.as_uri())
        await page.wait_for_timeout(300)
        h = await page.evaluate("document.body.scrollHeight")
        if h > 6000:
            mid = h // 2
            await page.screenshot(path=str(png_path.with_name(
                png_path.stem + "_上.png")), clip={"x": 0, "y": 0, "width": 1900, "height": mid}, full_page=True)
            await page.screenshot(path=str(png_path.with_name(
                png_path.stem + "_下.png")), clip={"x": 0, "y": mid, "width": 1900, "height": h - mid}, full_page=True)
            print(f"{png_path.stem}: {h}px → 拆上下两段")
        else:
            await page.screenshot(path=str(png_path), full_page=True)
            print(f"{png_path.stem}: {h}px → 整张")
        await browser.close()
    tmp.unlink()


async def main() -> None:
    day = sys.argv[1] if len(sys.argv) > 1 else "20260918"
    out_dir = REPO / "davis_analyzer" / "surge" / "reports"
    for name in (f"surge_{day}.md", f"surge_pattern_{day}.md"):
        await render(out_dir / name, out_dir / f"{name[:-3]}.png")


if __name__ == "__main__":
    asyncio.run(main())
