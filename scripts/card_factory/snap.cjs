// snap.cjs — 卡片工厂截图:HTML → 每张 .card 一个 PNG(1080x1440,1x)
// 用法: node snap.cjs <cards.html路径> --outdir <输出目录> [--prefix 前缀] [--scale 2]
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const args = process.argv.slice(2);
const htmlPath = args[0];
const outdir = args[args.indexOf('--outdir') + 1] || path.dirname(htmlPath);
const prefixIdx = args.indexOf('--prefix');
const prefix = prefixIdx >= 0 ? args[prefixIdx + 1] : 'card';
const scaleIdx = args.indexOf('--scale');
const scale = scaleIdx >= 0 ? Number(args[scaleIdx + 1]) : 1;

(async () => {
  const manifestPath = path.join(path.dirname(htmlPath), 'manifest.json');
  const names = fs.existsSync(manifestPath)
    ? JSON.parse(fs.readFileSync(manifestPath, 'utf8')).names
    : null;
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1200, height: 1600 }, deviceScaleFactor: scale });
  await page.goto('file://' + path.resolve(htmlPath));
  await page.waitForTimeout(600);
  fs.mkdirSync(outdir, { recursive: true });
  const cards = await page.locator('.card').all();
  // 溢出校验:内容超出 1440px 即失败,防止底部被裁
  let bad = 0;
  for (let i = 0; i < cards.length; i++) {
    const m = await cards[i].evaluate(el => ({ sh: el.scrollHeight, ch: el.clientHeight, sw: el.scrollWidth, cw: el.clientWidth }));
    if (m.sh > m.ch + 2 || m.sw > m.cw + 2) { console.error(`OVERFLOW card${i + 1}: sh=${m.sh}/${m.ch} sw=${m.sw}/${m.cw}`); bad++; }
  }
  if (bad > 0) { console.error(`${bad} 张卡片溢出,已中止截图`); process.exit(1); }
  for (let i = 0; i < cards.length; i++) {
    const name = names ? names[i] : String(i + 1).padStart(2, '0');
    const out = path.join(outdir, `${prefix}_${name}.png`);
    await cards[i].screenshot({ path: out });
    console.log('saved', path.relative(process.cwd(), out));
  }
  await browser.close();
})();
