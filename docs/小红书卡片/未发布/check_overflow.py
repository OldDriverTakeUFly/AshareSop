# 长图确定性溢出检测——检查所有元素文字是否溢出容器(2026-09-16)
# 比视觉目检更精确:逐元素比较 scrollHeight/scrollWidth 与 clientHeight/clientWidth
from __future__ import annotations

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

BASE = Path("/home/leo/Projects/CodeAgentDashboard/docs/小红书卡片/未发布")
PROJECTS = ["长文图卡_镍出海链", "长文图卡_碳价CBAM", "长文图卡_电解家族", "长文图卡_AI基建涨价", "长文图卡_全球龙头对照", "长文图卡_国产替代梯度"]

JS = """
() => {
  const issues = [];
  document.querySelectorAll('body *').forEach(el => {
    const oh = el.scrollHeight - el.clientHeight;
    const ow = el.scrollWidth - el.clientWidth;
    // 忽略 body 整页滚动;捕获块级容器内容溢出
    if ((oh > 3 || ow > 3) && el.clientHeight > 0 && !['HTML','BODY'].includes(el.tagName)) {
      const txt = (el.innerText || '').slice(0, 40).replace(/\\n/g, ' ');
      issues.push(`${el.tagName}.${el.className} overflow h:${oh}px w:${ow}px "${txt}"`);
    }
  });
  return issues;
}
"""


async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 750, "height": 1200})
        for proj in PROJECTS:
            await page.goto((BASE / proj / "长图.html").as_uri())
            await page.wait_for_timeout(400)
            issues = await page.evaluate(JS)
            n_tables = await page.evaluate("document.querySelectorAll('table').length")
            h = await page.evaluate("document.body.scrollHeight")
            status = "PASS" if not issues else f"FAIL({len(issues)}处溢出)"
            print(f"{proj}: {status} | 高{h}px 表格{n_tables}张")
            for i in issues[:8]:
                print("   -", i)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
