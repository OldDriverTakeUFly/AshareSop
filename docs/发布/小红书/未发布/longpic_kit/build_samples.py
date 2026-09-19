#!/usr/bin/env python3
# build_samples.py —— longpic_kit 样张:全组件 × 三主题,渲染 + 溢出检测(2026-09-18 方案B验收#1)
from __future__ import annotations

import asyncio
from pathlib import Path

KIT = Path(__file__).parent
THEMES = {
    "板块热点": KIT / "kit.css",  # 默认值即板块热点系:无需 :root 覆盖,给空块
    "产业链": None,  # themes.md 第2节
    "日更": None,    # themes.md 第3节(天梯口径)
}
ROOT_BLOCKS = {
    "板块热点": "",
    "产业链": """
:root{ --bg:#0b1026; --card:#141b40; --border:#2b3775; --text:#eaf0ff; --dim:#8d97c9;
  --accent1:#ffb347; --accent2:#ff6b9d; --tagbg:#283566; --tagfg:#9db4ff;
  --up:#ff6b6b; --down:#4ade80; --pos:#6ee7b7; --neg:#fda4af; --th:#9db4ff;
  --h1-size:44px; --h1-lh:1.28; --h1-weight:800; --sub-size:22px; --sub-mb:24px;
  --hook-bg:linear-gradient(135deg,#2a1f3d,#1a1533);
  --stats-mt:16px; --stat-bg:#1b2452; --stat-border:none; --stat-pad:14px;
  --v-size:31px; --v-color:#6ee7b7; --h2-size:26px; --h2-color:#ffd166;
  --td-border:#222c5c; --td-color:#dbe2ff; --td-lh:1.5; --td-wb:normal;
  --note-mt:10px; --note-lh:1.6; --note-color:#7b85b5;
  --insight-size:20px; --insight-color:#e6ebff; --insight-bar:#ff8fab; --insight-bg:#241a3f;
  --rows-mb:8px; --rows-color:#dbe2ff; --b-em:#ffd166;
  --foot-bg:#10163a; --foot-color:#6b75a5; --vs-bg:#101626; --border-soft:#222c5caa; }
""",
    "日更": """
:root{ --bg:#0f1014; --card:#191b22; --border:#343846; --text:#f2f3f7; --dim:#9aa0b5;
  --accent1:#ff4d4f; --accent2:#ffd166; --tagbg:#3a1d1f; --tagfg:#ff9c9c;
  --up:#ff6b6b; --down:#4ade80; --pos:#ff6b6b; --neg:#4ade80; --th:#ff9c9c;
  --h1-size:42px; --h1-lh:1.38; --h1-weight:800; --sub-size:21px; --sub-mb:22px;
  --hook-bg:#191b22; --stats-mt:14px; --stat-bg:#343846; --stat-border:none; --stat-pad:14px 8px;
  --v-size:32px; --v-color:#ffd166; --h2-size:25px; --h2-color:#ffd166;
  --td-border:#34384666; --td-color:#f2f3f7; --td-lh:1.55; --td-wb:break-all;
  --note-mt:8px; --note-lh:1.6; --note-color:#9aa0b5;
  --insight-size:19px; --insight-color:#f2f3f7; --insight-bar:#ffd166; --insight-bg:#34384655;
  --rows-mb:6px; --rows-color:#f2f3f7; --b-em:#ffd166;
  --foot-bg:#191b22; --foot-color:#9aa0b5; --vs-bg:#191b22; --border-soft:#34384655; }
""",
}

BODY = """
  <span class="tag">样张 · {name}系</span>
  <h1 class="grad">kit 组件全样张<br>渐变标题一行为证</h1>
  <div class="sub">骨架顺序:tag→h1→sub→hook→编号小节→insight→foot;本页覆盖全部组件。</div>

  <div class="hook">
    <div class="h">hook 首屏卡(黄金三秒)</div>
    <div class="stats">
      <div class="stat"><div class="v">+2.62%</div><div class="k">stat 主强调</div></div>
      <div class="stat"><div class="v ice">60.5亿</div><div class="k">stat.ice 次强调</div></div>
      <div class="stat"><div class="v">涨停</div><div class="k">第三枚</div></div>
    </div>
    <p style="margin-top:14px">hook 内导语段:反差/背离/最响数字放这里。</p>
  </div>

  <div class="card">
    <h2>一、表格(双轨取色)</h2>
    <div class="st">行情列 up/down 红涨绿跌;定性列 pos/neg</div>
    <table>
      <tr><th>公司</th><th>今日涨幅</th><th>距近季峰</th><th>定性</th></tr>
      <tr><td>样张甲</td><td class="up">+10.03%</td><td class="down">-4.6%</td><td><span class="pos">已收复</span></td></tr>
      <tr><td>样张乙</td><td class="up">+2.82%</td><td class="down">-26.7%</td><td><span class="neg">仍在坑底</span></td></tr>
      <tr><td>样张丙</td><td class="down">-1.20%</td><td class="down">-8.0%</td><td><span class="neg">走弱</span></td></tr>
    </table>
    <div class="note">note 注脚:口径与色义说明。</div>
  </div>

  <div class="insight">insight 左边条:每图至少一条的金句位。</div>

  <div class="card">
    <h2>二、rows 列表</h2>
    <ul class="rows">
      <li>要点一,含<b>加粗强调</b>数字</li>
      <li>要点二</li>
    </ul>
  </div>

  <div class="card">
    <h2>三、tier 梯队分层</h2>
    <div class="tier t1"><b>第一梯队</b> 宽度 100%</div>
    <div class="tier t2"><b>第二梯队</b> 宽度 84%</div>
    <div class="tier t3"><b>第三梯队</b> 宽度 68%</div>
  </div>

  <div class="card">
    <h2>四、pyr 金字塔</h2>
    <div class="pyr">
      <div class="layer l1"><b>第一层</b> 总量共振</div>
      <div class="layer l2"><b>第二层</b> 等级跃迁</div>
      <div class="layer l3"><b>第三层</b> 单点垄断</div>
      <div class="layer l4"><b>第四层</b> 地缘管制</div>
    </div>
  </div>

  <div class="card">
    <h2>五、vs 对峙双栏</h2>
    <div class="vs">
      <div class="side a"><h3>正面</h3><p>支持延续的证据占位,两行以上看行距。</p></div>
      <div class="side b"><h3>反面</h3><p>值得警惕的信号占位,两行以上看行距。</p></div>
    </div>
  </div>

  <div class="foot">foot:信源+免责占位,不构成投资建议。</div>
"""

JS = """
() => {
  const issues = [];
  document.querySelectorAll('body *').forEach(el => {
    const oh = el.scrollHeight - el.clientHeight;
    const ow = el.scrollWidth - el.clientWidth;
    if ((oh > 3 || ow > 3) && el.clientHeight > 0 && !['HTML','BODY'].includes(el.tagName)) {
      issues.push(`${el.tagName}.${el.className} overflow h:${oh}px w:${ow}px`);
    }
  });
  return issues;
}
"""


async def main() -> None:
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 750, "height": 1200}, device_scale_factor=2)
        for name, root in ROOT_BLOCKS.items():
            html = (f'<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">'
                    f'<link rel="stylesheet" href="kit.css"><style>{root}</style></head><body>'
                    f'{BODY.format(name=name)}</body></html>')
            hp = KIT / f"样张_{name}.html"
            hp.write_text(html, encoding="utf-8")
            await page.goto(hp.as_uri())
            await page.wait_for_timeout(500)
            issues = await page.evaluate(JS)
            h = await page.evaluate("document.body.scrollHeight")
            await page.screenshot(path=str(hp.with_suffix(".png")), full_page=True)
            print(f"样张_{name}: {'PASS' if not issues else 'FAIL ' + '; '.join(issues[:5])} | 高{h}px")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
