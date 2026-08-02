"""恐慌预警图表生成器 — PanicReport → plotly Figure → PNG.

将 panic_detector 的 PanicReport 转成 4 面板仪表盘 PNG，用于飞书图片推送。

布局（3 行 2 列，v5 验证通过）：
  ┌──────────────┬──────────────┐
  │ 波动率温度    │ 板块强弱      │ row 1（数据柱状图）
  ├──────────────┼──────────────┤
  │ 方向仪表盘    │ 恐慌趋势      │ row 2（gauge + 折线，趋势跨 row2-3）
  │ （纯图形）    │              │
  ├──────────────┤              │
  │ 方向文字标注  │              │ row 3（仪表盘读数，独立空间）
  └──────────────┴──────────────┘

设计原则：
- 仪表盘只负责视觉（gauge 无 title/number），文字标注在下方独立 row
- 板块按涨跌幅着色（绿涨红跌）
- 趋势图双轴（跌停数 + iVIX）
- 数据缺失时优雅降级（空面板不崩）

依赖：plotly + kaleido（需 chrome，kaleido_get_chrome 安装）
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from stockhot.alert.panic_detector import PanicReport

# 象限配色（与 _QUADRANT_META emoji 对齐）
_QUADRANT_COLORS = {
    "逼空过热": "#e67e22",   # 🟠 橙
    "下跌恐慌": "#e74c3c",   # 🔴 红
    "强势上涨": "#27ae60",   # 🟢 绿
    "阴跌预警": "#f39c12",   # 🟡 黄
}

# 恐慌等级配色（P 分位 → 颜色）
def _pct_color(pct: float) -> str:
    if pct >= 95:
        return "#c0392b"  # 极度恐慌 深红
    if pct >= 90:
        return "#e67e22"  # 明显恐慌 橙
    if pct >= 50:
        return "#27ae60"  # 正常 绿
    return "#3498db"      # 平静 蓝


def build_panic_dashboard(report: PanicReport):
    """构建恐慌预警仪表盘 plotly Figure.

    参数：
        report: PanicReport（含波动率/方向/板块/趋势数据）

    返回：
        plotly.graph_objects.Figure（调用方负责 write_image 导出）
    """
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    from stockhot.alert.panic_detector import SectorStructure

    # ── 提取数据（带降级处理）──
    # 波动率
    vol_indices = sorted(report.volatility_indices, key=lambda x: -x.rv20_pct) if report.volatility_indices else []

    # 板块
    sectors = report.sectors
    sec_strong = sectors.strong if sectors and sectors.available else []
    sec_weak = sectors.weak if sectors and sectors.available else []

    # 方向
    direction = report.direction
    sse_chg = direction.sse_pct_chg if direction else None
    cum_5d = direction.cum_5d_pct if direction else None
    limit_up = direction.limit_up if direction else None
    limit_down = direction.limit_down if direction else None

    # 象限配色
    quad_color = _QUADRANT_COLORS.get(report.quadrant, "#333333")

    # ── 构建 3 行 2 列布局 ──
    fig = make_subplots(
        rows=3, cols=2,
        row_heights=[0.42, 0.38, 0.20],
        specs=[
            [{"type": "bar"}, {"type": "bar"}],
            [{"type": "domain"}, {"type": "scatter"}],
            [{"type": "domain"}, None],
        ],
        vertical_spacing=0.05,
        horizontal_spacing=0.14,
        subplot_titles=("波动率温度（RV20分位）", "板块强弱", None, "近期恐慌趋势", None, None),
    )

    # ── 面板 1：波动率温度 ──
    if vol_indices:
        names = [i.name for i in vol_indices]
        pcts = [i.rv20_pct for i in vol_indices]
        colors = [_pct_color(p) for p in pcts]
        fig.add_trace(go.Bar(
            x=names, y=pcts, marker_color=colors,
            text=[f"P{p:.0f}" for p in pcts], textposition="outside",
            showlegend=False,
        ), row=1, col=1)
        fig.update_yaxes(range=[0, 110], row=1, col=1)
    else:
        _add_empty_note(fig, "波动率数据不可用", row=1, col=1, paper_x=0.215, paper_y=0.75)

    # ── 面板 2：板块强弱 ──
    all_sectors = sec_strong + sec_weak
    if all_sectors:
        sec_names = [s.name[:6] for s in all_sectors]
        # 用涨跌幅或涨停数作为展示值
        sec_vals = []
        for s in all_sectors:
            if s.limit_up > 0 or s.limit_down > 0:
                sec_vals.append(float(s.limit_up - s.limit_down))
            elif s.pct_change is not None:
                sec_vals.append(s.pct_change)
            else:
                sec_vals.append(0.0)
        sec_colors = ["#27ae60" if v > 0 else "#e74c3c" for v in sec_vals]
        sec_text = []
        for s, v in zip(all_sectors, sec_vals):
            if s.limit_up > 0 or s.limit_down > 0:
                sec_text.append(f"涨{s.limit_up}/跌{s.limit_down}")
            elif s.pct_change is not None:
                sec_text.append(f"{s.pct_change:+.1f}%")
            else:
                sec_text.append("")
        fig.add_trace(go.Bar(
            x=sec_vals, y=sec_names, orientation="h",
            marker_color=sec_colors,
            text=sec_text, textposition="outside",
            showlegend=False,
        ), row=1, col=2)
    else:
        _add_empty_note(fig, "板块数据不可用", row=1, col=2, paper_x=0.785, paper_y=0.75)

    # ── 面板 3：方向仪表盘（纯 gauge）──
    if sse_chg is not None:
        fig.add_trace(go.Indicator(
            mode="gauge",
            value=sse_chg,
            gauge={
                "axis": {"range": [-3, 3], "tickwidth": 1, "tickcolor": "#666",
                         "ticktext": ["-3%", "-1%", "0", "+1%", "+3%"],
                         "tickvals": [-3, -1, 0, 1, 3]},
                "bar": {"color": quad_color, "thickness": 0.3},
                "bgcolor": "white", "borderwidth": 1, "bordercolor": "#eee",
                "steps": [
                    {"range": [-3, -0.5], "color": "#fadbd8"},
                    {"range": [-0.5, 0.5], "color": "#fef9e7"},
                    {"range": [0.5, 3], "color": "#d5f5e3"},
                ],
                "threshold": {"line": {"color": "#2c3e50", "width": 3}, "value": sse_chg},
            },
            domain={"row": 0, "column": 0},
        ), row=2, col=1)
    else:
        _add_empty_note(fig, "方向数据不可用", row=2, col=1, paper_x=0.215, paper_y=0.30)

    # ── 面板 4：趋势（需要从 report 外部传入历史数据，这里用占位）──
    # 趋势数据不在 PanicReport 里（由 run_panic_alert 的 _build_trend 单独处理）
    # 图表里先留空，或用 panic_history 的简要数据
    _add_trend_placeholder(fig)

    # ── row3：方向文字标注（仪表盘下方独立空间）──
    if sse_chg is not None or cum_5d is not None:
        # 隐形占位 indicator（让 row3 有 domain）
        fig.add_trace(go.Indicator(
            mode="number", value=0,
            number={"font": {"color": "white", "size": 1}},
            domain={"x": [0, 0.43], "y": [0, 0.13]},
        ), row=3, col=1)

        # 标注文字
        title_text = f"<b>{report.quadrant or '方向仪表盘'}</b>"
        fig.add_annotation(
            text=title_text, x=0.215, y=0.11, xref="paper", yref="paper",
            xanchor="center", yanchor="middle",
            showarrow=False, font=dict(size=14, color="#333"),
        )
        # 读数行
        parts = []
        if sse_chg is not None:
            color = "#27ae60" if sse_chg >= 0 else "#e74c3c"
            parts.append(f"上证当日 <b style='color:{color}'>{sse_chg:+.2f}%</b>")
        if cum_5d is not None:
            parts.append(f"5日 {cum_5d:+.1f}%")
        if limit_up is not None and limit_down is not None:
            parts.append(f"涨跌停 {limit_up}:{limit_down}")
        fig.add_annotation(
            text=" ｜ ".join(parts) if parts else "",
            x=0.215, y=0.04, xref="paper", yref="paper",
            xanchor="center", yanchor="middle",
            showarrow=False, font=dict(size=11, color="#666"),
        )

    # ── 全局布局 ──
    quad_emoji = {"逼空过热": "🟠", "下跌恐慌": "🔴", "强势上涨": "🟢", "阴跌预警": "🟡"}.get(report.quadrant, "⚪")
    # 标题：象限 + 强度 + 持续天数（高波时）
    title_parts = [
        f"{quad_emoji} {report.quadrant or '市场读数'} | "
        f"强度 {report.intensity_score:.0f}/100 {report.intensity_label}",
    ]
    if report.vol_streak_brief:
        # 去掉 emoji 前缀（标题里不需要重复 📈）
        streak_text = report.vol_streak_brief.replace("📈 ", "")
        title_parts.append(streak_text)
    title_parts.append(f"{report.trade_date} {report.timestamp}")

    fig.update_layout(
        title_text=" | ".join(title_parts),
        title_font=dict(size=14, color=quad_color),
        template="plotly_white",
        height=780, width=900,
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=0.01, xanchor="right", x=1),
        margin=dict(l=50, r=50, t=70, b=30),
        font=dict(size=12),
    )

    return fig


def _add_empty_note(fig, text: str, row: int, col: int, paper_x: float, paper_y: float) -> None:
    """在空面板位置添加文字提示（数据不可用时的降级）."""
    fig.add_annotation(
        text=text, x=paper_x, y=paper_y, xref="paper", yref="paper",
        xanchor="center", yanchor="middle",
        showarrow=False, font=dict(size=12, color="#999"),
    )


def _add_trend_placeholder(fig) -> None:
    """趋势面板占位（趋势数据由调用方通过 build_trend_traces 注入）."""
    import plotly.graph_objects as go
    fig.add_trace(go.Scatter(
        x=[], y=[], mode="lines+markers", name="跌停数",
        line=dict(color="#e74c3c", width=2),
    ), row=2, col=2)
    fig.add_trace(go.Scatter(
        x=[], y=[], mode="lines+markers", name="iVIX",
        line=dict(color="#3498db", width=2, dash="dot"),
    ), row=2, col=2)


def add_trend_data(fig, dates: list[str], limit_down: list, ivix: list) -> None:
    """向趋势面板注入真实历史数据.

    由 run_panic_alert 调用（它有 panic_history 的多日数据）。
    """
    import plotly.graph_objects as go
    # 找到趋势面板的两个 trace（倒数第 2 和第 1）
    # trace 顺序：vol_bar, sector_bar, indicator_gauge, scatter_跌停, scatter_ivix, invisible_indicator
    # 跌停和 iVIX 是倒数第 3 和第 2 个（如果有 row3 invisible 则倒数 3/2，否则 2/1）
    trend_traces = []
    for i, t in enumerate(fig.data):
        if hasattr(t, "name") and t.name in ("跌停数", "iVIX"):
            trend_traces.append(i)
    if len(trend_traces) >= 2:
        # 更新跌停 trace
        fig.data[trend_traces[0]].x = dates
        fig.data[trend_traces[0]].y = limit_down
        fig.data[trend_traces[0]].text = [str(v) for v in limit_down]
        # 更新 iVIX trace
        fig.data[trend_traces[1]].x = dates
        fig.data[trend_traces[1]].y = ivix


def render_dashboard_png(fig, output_path: str | Path | None = None) -> str:
    """渲染 Figure 到 PNG 文件.

    参数：
        fig: plotly Figure（build_panic_dashboard 返回值）
        output_path: 输出路径；None 时用临时文件

    返回：
        PNG 文件路径
    """
    if output_path is None:
        # 临时文件
        fd, output_path = tempfile.mkstemp(suffix=".png", prefix="panic_dashboard_")
        os.close(fd)
    else:
        output_path = str(output_path)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    try:
        fig.write_image(output_path)
        size_kb = os.path.getsize(output_path) / 1024
        logger.info(f"[panic_chart] dashboard PNG: {output_path} ({size_kb:.0f} KB)")
        return output_path
    except Exception as e:
        logger.error(f"[panic_chart] PNG 渲染失败: {type(e).__name__}: {e}")
        raise
