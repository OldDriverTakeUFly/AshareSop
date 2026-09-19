"""两份 md 报告渲染(spec §6): 全量九维 + 形态副本. 模板化,无 LLM."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from loguru import logger

from davis_analyzer.core.config import SURGE_REPORTS_DIR

_DISCLAIMER = "> 本报告为程序化筛选分析,不构成投资建议。口径见" \
    "docs/superpowers/specs/2026-09-18-surge-screener-design.md。"


def _fmt(v, pct=False, nd=1) -> str:
    if v is None or isinstance(v, float) is False:
        try:
            v = float(v)
        except (TypeError, ValueError):
            return "—"
    if v != v:
        return "—"
    return f"{v:+.1%}" if pct else f"{v:.{nd}f}"


def _text(v) -> str:
    """NaN/None 安全的文本渲染(行业列 None 读回 NaN 是真值,`or ""` 不生效)."""
    if v is None:
        return ""
    if isinstance(v, float) and v != v:
        return ""
    return str(v)


def _parse_tags(s) -> str:
    if not isinstance(s, str) or not s or s == "[]":
        return "—"
    try:
        items = json.loads(s)
        return "、".join(items) if items else "—"
    except Exception:
        return "—"


def render_full_report(day: str, out: dict, *, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or SURGE_REPORTS_DIR
    out_dir = out_dir / day  # 按日期分目录归档(2026-09-19 用户拍板)
    out_dir.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [f"# Surge 全量筛选报告 {day}", ""]
    lines.append(f"- 命中数: {out['pool_n']}(当日涨幅>7%)")
    lines.append(f"- 筹码数据日: {out.get('cyq_day') or '缺失'}")
    st = out.get("cninfo_stats") or {}
    if st:
        lines.append(f"- 巨潮同步: ok={st.get('ok')} fail={st.get('fail')} "
                     f"events={st.get('events')}")
    lines.append("")
    snap: pd.DataFrame = out["snapshot_df"]
    if snap.empty:
        lines.append("当日无命中。")
    else:
        tags_map: dict[str, list[str]] = {}
        tags_df: pd.DataFrame = out.get("tags_df")
        if tags_df is not None and not tags_df.empty:
            for _, t in tags_df.iterrows():
                tags_map.setdefault(t["ts_code"], []).append(t["tag"])
        lines += ["## 全量表(按综合分)", "",
                  "| 排名 | 代码 | 名称 | 行业 | 涨幅% | 位置分位 | 超大单净额(万) "
                  "| 获利盘% | Δ5d | 压力距 | 支撑距 | hype | risk | 形态标签 | 综合分 |",
                  "|" + "---|" * 15]
        for _, r in snap.sort_values("rank").iterrows():
            lines.append(
                f"| {int(r['rank'])} | {r['ts_code']} | {_text(r.get('name'))} | "
                f"{_text(r.get('industry'))} | {_fmt(r.get('pct_chg'))} | "
                f"{_fmt(r.get('pos_250d'), pct=True)} | "
                f"{_fmt(r.get('elg_net_d0'), nd=0)} | "
                f"{_fmt(r.get('winner_rate'))} | "
                f"{_fmt(r.get('winner_delta_5d'))} | "
                f"{_fmt(r.get('resistance_dist'), pct=True)} | "
                f"{_fmt(r.get('support_dist'), pct=True)} | "
                f"{_fmt(r.get('hype_count'), nd=0)} | "
                f"{_fmt(r.get('risk_flag_count'), nd=0)} | "
                f"{'、'.join(tags_map.get(r['ts_code'], [])) or '—'} | "
                f"{_fmt(r.get('composite'))} |")
        lines += ["", "## Top 12 深析", ""]
        for _, r in snap.sort_values("rank").head(12).iterrows():
            lines += [
                f"### {int(r['rank'])}. {r['ts_code']} {_text(r.get('name'))}"
                f"({_text(r.get('industry')) or '—'}) 涨幅 {_fmt(r.get('pct_chg'))}%",
                "",
                f"- 位置: 250日分位 {_fmt(r.get('pos_250d'), pct=True)}"
                f"(MA60 {_fmt(r.get('dist_ma60'), pct=True)}"
                f"/MA250 {_fmt(r.get('dist_ma250'), pct=True)}),"
                f"距250日高点 {_fmt(r.get('dd_high_250'), pct=True)}"
                + (" [次新]" if r.get("is_new") else "")
                + (" [ST]" if r.get("is_st") else ""),
                f"- 资金: 超大单净额 {_fmt(r.get('elg_net_d0'), nd=0)} 万,"
                f"5日大单+超大单 {_fmt(r.get('lg_net_5d'), nd=0)} 万,"
                f"连续净流入 {_fmt(r.get('consec_net_days'), nd=0)} 天,"
                f"净流入/成交额 {_fmt(r.get('net_ratio_d0'), pct=True)}",
                f"- 筹码: 成本中枢 {_fmt(r.get('weight_avg'))},"
                f"主力低位筹码 {_fmt(r.get('cost_5pct'))},"
                f"获利盘 {_fmt(r.get('winner_rate'))}%"
                f"(Δ5d {_fmt(r.get('winner_delta_5d'))})",
                f"- 压力 {_fmt(r.get('resistance_price'))}"
                f"({_fmt(r.get('resistance_dist'), pct=True)}) | "
                f"支撑 {_fmt(r.get('support_price'))}"
                f"({_fmt(r.get('support_dist'), pct=True)})",
                f"- 炒作预期: {_parse_tags(r.get('hype_tags'))}",
                f"- 扫雷: {_parse_tags(r.get('risk_flags'))}",
                "",
            ]
    lines += ["", _DISCLAIMER, ""]
    path = out_dir / f"surge_{day}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("全量报告 → {}", path)
    return path


def render_pattern_report(day: str, out: dict, *, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or SURGE_REPORTS_DIR
    out_dir = out_dir / day
    out_dir.mkdir(parents=True, exist_ok=True)
    pat: pd.DataFrame = out["pattern_df"]
    snap: pd.DataFrame = out["snapshot_df"]
    lines = [f"# Surge 形态副本报告 {day}", "",
             f"形态命中(前期放量阳→缩量回调→平台突破): **{len(pat)} 只**", ""]
    if pat.empty:
        lines.append("当日无形态命中。")
    else:
        merged = pat.merge(snap, on=["trade_date", "ts_code"], how="left")
        lines += [
            "| 代码 | 名称 | 涨幅% | 放量阳 | 量比 | 回调段 | 回撤 | 量能衰减 "
            "| 平台高点 | 突破幅度 | hype | risk | 综合分 |",
            "|" + "---|" * 13]
        for _, r in merged.iterrows():
            lines.append(
                f"| {r['ts_code']} | {_text(r.get('name'))} | "
                f"{_fmt(r.get('pct_chg'))} | "
                f"{r['boom_date']}(+{_fmt(r.get('boom_pct'))}%) | "
                f"{_fmt(r.get('boom_vol_ratio'))}× | "
                f"{r['pullback_start']}~{r['pullback_end']} | "
                f"{r['pullback_depth']:.1%} | {r['vol_decay']:.0%} | "
                f"{_fmt(r.get('plateau_high'), nd=2)} | "
                f"+{r['breakout_pct']:.1%} | "
                f"{_fmt(r.get('hype_count'), nd=0)} | "
                f"{_fmt(r.get('risk_flag_count'), nd=0)} | "
                f"{_fmt(r.get('composite'))} |")
        lines += ["", "## 观察卡(两路径,事前不判别)", ""]
        for _, r in merged.iterrows():
            lines += [
                f"### {r['ts_code']} {_text(r.get('name'))}",
                f"- 路径①回抽平台确认: 观察位 {_fmt(r.get('plateau_high'), nd=2)}"
                f"(平台高点),缩量回抽企稳可关注",
                "- 路径②直接续涨不回抽: 更强",
                f"- 事件面: 炒作预期 {_parse_tags(r.get('hype_tags'))} | "
                f"扫雷 {_parse_tags(r.get('risk_flags'))}",
                "",
            ]
    lines += ["", _DISCLAIMER, ""]
    path = out_dir / f"surge_pattern_{day}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("形态副本报告 → {}", path)
    return path
