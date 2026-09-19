# 长图截图渲染——三篇产业链研报长文卡片(2026-09-16)
# 750px viewport × deviceScaleFactor 2,全页截图;过高(>6000px)自动拆上下两张
from __future__ import annotations

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

BASE = Path("/home/leo/Projects/CodeAgentDashboard/docs/小红书卡片/未发布")
PROJECTS = ["长文图卡_镍出海链", "长文图卡_碳价CBAM", "长文图卡_电解家族", "长文图卡_AI基建涨价", "长文图卡_全球龙头对照", "长文图卡_国产替代梯度", "板块热点复盘/2026-09-18_光通信复活"]
SPLIT_THRESHOLD = 9000  # 2026-09-18 用户拍板:长度不限、内容更详细,超9000px才拆上下两张


async def shoot(project: str) -> None:
    html = BASE / project / "长图.html"
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 750, "height": 1200}, device_scale_factor=2)
        await page.goto(html.as_uri())
        await page.wait_for_timeout(600)
        h = await page.evaluate("document.body.scrollHeight")
        out = html.with_suffix(".png")
        if h > SPLIT_THRESHOLD:
            mid = h // 2
            await page.screenshot(path=str(out.with_name("长图_上.png")), clip={"x": 0, "y": 0, "width": 750, "height": mid})
            await page.screenshot(path=str(out.with_name("长图_下.png")), clip={"x": 0, "y": mid, "width": 750, "height": h - mid})
            print(f"{project}: {h}px -> 拆上下两张")
        else:
            await page.screenshot(path=str(out), full_page=True)
            print(f"{project}: {h}px -> 整张 {out.name}")
        await browser.close()


async def main() -> None:
    for proj in PROJECTS:
        await shoot(proj)


if __name__ == "__main__":
    asyncio.run(main())
