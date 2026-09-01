// snap.js — 截图小红书卡片
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1200, height: 1600 }, deviceScaleFactor: 1 });
  await page.goto('file:///home/leo/Projects/CodeAgentDashboard/docs/小红书卡片/GPU四小龙/cards.html');
  await page.waitForTimeout(800);
  const names = ['01_封面', '02_四小龙档案', '03_景气度分化', '04_估值断层', '05_关键节点日历', '06_结论与风险'];
  for (let i = 1; i <= 6; i++) {
    const el = page.locator(`#card${i}`);
    await el.screenshot({ path: `/home/leo/Projects/CodeAgentDashboard/docs/小红书卡片/GPU四小龙/GPU四小龙_${names[i - 1]}.png` });
    console.log('saved', names[i - 1]);
  }
  await browser.close();
})();
