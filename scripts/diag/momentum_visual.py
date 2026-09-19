"""动量分可视化一页纸——白话+图解(2026-09-17, 内部讲解/内容线素材).

面板一: 三窗映射曲线(区间涨跌幅→0-100分, 饱和点+30%/+50%/+100%, 缺窗=50)
面板二: 真票实例三只(有研新材=满分成因 / 中天科技=混合形态 / 次新单窗=惩罚后)
数据: 与 2026-09-17 因子审计同一批 analyze_momentum 输出硬编码(可复算)。
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

font_manager.fontManager.addfont("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
plt.rcParams["font.family"] = "Noto Sans CJK JP"
plt.rcParams["axes.unicode_minus"] = False

C_MAIN, C_ACC, C_DIM, C_BAD = "#1d4ed8", "#ea580c", "#94a3b8", "#dc2626"
fig = plt.figure(figsize=(13.6, 7.2), dpi=150)
gs = fig.add_gridspec(2, 3, height_ratios=[1, 1.15], hspace=0.42, wspace=0.28,
                      left=0.06, right=0.97, top=0.88, bottom=0.09)

fig.suptitle("动量分是怎么算出来的:一只股票,测三次速度",
             fontsize=19, fontweight="bold", color="#0f172a", y=0.965)
fig.text(0.5, 0.915,
         "近3个月 / 近6个月 / 近1年各测一次区间涨幅,各自换算成0-100分后加权平均(权重20%/30%/50%,长程最重)",
         ha="center", fontsize=11.5, color="#475569")

# ── 面板一: 映射曲线 ──
ax1 = fig.add_subplot(gs[0, :])
wins = [("近3个月(60日)", 30, 0.2, "#2563eb"), ("近6个月(120日)", 50, 0.3, "#16a34a"),
        ("近1年(250日)", 100, 0.5, "#ea580c")]
for label, full, w, color in wins:
    rets = [x / 10 for x in range(-int(full), int(full) * 2 + 1)]
    scores = [max(0.0, min(100.0, 50 + r / full * 50)) for r in rets]
    ax1.plot(rets, scores, color=color, lw=2.4,
             label=f"{label} 权重{int(w*100)}%｜饱和点+{full}%")
    ax1.scatter([full], [100], color=color, s=46, zorder=5)
ax1.axhline(50, color=C_DIM, lw=1, ls="--")
ax1.axvline(0, color=C_DIM, lw=1, ls="--")
ax1.annotate("原地不动 = 50分", xy=(2, 50), xytext=(12, 38), fontsize=10, color="#475569",
             arrowprops=dict(arrowstyle="->", color=C_DIM))
ax1.annotate("缺这个窗口 = 按50分计\n(次新股不占便宜)", xy=(-95, 50), xytext=(-98, 71),
             fontsize=10, color=C_BAD,
             arrowprops=dict(arrowstyle="->", color=C_BAD))
ax1.set_xlabel("区间涨跌幅(%)", fontsize=11)
ax1.set_ylabel("该窗口得分", fontsize=11)
ax1.set_title("每个窗口的换算尺:涨幅过饱和点即满分,跌幅对称归零", fontsize=12.5, loc="left")
ax1.legend(loc="lower right", fontsize=9.5, frameon=False)
ax1.set_ylim(-4, 108)

# ── 面板二: 三只真票 ──
cases = [
    ("有研新材 600206\n(满分样本)", {"60": 41.0, "120": 64.9, "250": 126.9}, 100.0,
     "三窗全超饱和线\n→ 满分是挣来的"),
    ("中天科技 600522\n(混合形态)", {"60": 12.9, "120": -19.4, "250": 96.7}, 72.7,
     "短窗平/中窗跌/长窗强\n→ 长程拉动到73"),
    ("次新股(仅60日窗)\n(惩罚演示)", {"60": 30.0}, 60.0,
     "只有短跑成绩,长程按50计\n→ 旧口径虚高100,现60"),
]
for i, (name, wr, total, note) in enumerate(cases):
    ax = fig.add_subplot(gs[1, i])
    win_cfg = {"60": ("3个月", 30, 0.2, "#2563eb"), "120": ("6个月", 50, 0.3, "#16a34a"),
               "250": ("1年", 100, 0.5, "#ea580c")}
    order = ["250", "120", "60"]
    ylabels = []
    for j, wkey in enumerate(order):
        label, full, w, color = win_cfg[wkey]
        ylabels.append(f"{label}\n权重{int(w*100)}%")
    order = ["250", "120", "60"]
    for j, wkey in enumerate(order):
        label, full, w, color = win_cfg[wkey]
        v = wr.get(wkey)
        if v is None:
            ax.barh(j, 8, color="#e2e8f0")  # 缺窗占位小灰条
            ax.text(14, j, "缺这个窗口 → 按50分计", va="center", fontsize=9.5, color=C_BAD)
            continue
        pct = max(-5, min(200, v / full * 100))
        ax.barh(j, pct, color=color, alpha=0.88)
        ax.axvline(100, color=C_DIM, lw=0.8, ls=":")
        if pct < 0:
            ax.text(4, j, f"{v:+.0f}%(跌,归零区)", va="center", fontsize=9.5, color=C_BAD)
        else:
            ax.text(pct + 4 if pct < 180 else pct - 6, j, f"{v:+.0f}%", va="center",
                    fontsize=10, ha="left" if pct < 180 else "right",
                    color="#0f172a", fontweight="bold")
    ax.set_yticks(range(3)); ax.set_yticklabels(ylabels, fontsize=9.5)
    ax.set_xlim(-8, 215); ax.set_xticks([])
    ax.axvline(0, color="#334155", lw=0.8)
    ax.set_title(name, fontsize=11.5, fontweight="bold")
    ax.text(0.5, -0.42, f"动量总分 {total:.0f}", transform=ax.transAxes, ha="center",
            fontsize=15, fontweight="bold",
            color=C_ACC if total >= 70 else C_BAD)
    ax.text(0.5, -0.62, note, transform=ax.transAxes, ha="center", fontsize=8.8, color="#475569")
    ax.spines[["top", "right", "bottom"]].set_visible(False)

out = "/home/leo/Projects/CodeAgentDashboard/docs/研报/方法论/动量分计算可视化_2026-09-17.png"
fig.savefig(out, bbox_inches="tight", facecolor="white")
print("saved:", out)
