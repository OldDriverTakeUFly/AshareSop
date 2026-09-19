"""report 两份输出测试: 空数据防线+关键内容渲染."""

from __future__ import annotations

import pandas as pd

from davis_analyzer.systems.surge import report


def _out(snap: pd.DataFrame, pat=None, tags=None):
    return {"day": "20260918", "pool_n": len(snap), "snapshot_df": snap,
            "pattern_df": pat if pat is not None else pd.DataFrame(),
            "tags_df": tags if tags is not None else pd.DataFrame(),
            "cyq_day": "20260918", "cninfo_stats": {}}


def test_full_report_empty_pool(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "SURGE_REPORTS_DIR", tmp_path)
    p = report.render_full_report("20260918", _out(pd.DataFrame()))
    assert p.exists()
    assert "无命中" in p.read_text(encoding="utf-8")


def test_full_report_renders_rows_and_tags(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "SURGE_REPORTS_DIR", tmp_path)
    snap = pd.DataFrame([{
        "ts_code": "000001.SZ", "name": "测试", "industry": "银行", "pct_chg": 8.4,
        "pos_250d": 0.3, "dist_ma60": 0.05, "dist_ma250": 0.1,
        "dd_high_250": -0.2, "elg_net_d0": 5000.0, "lg_net_5d": 20000.0,
        "net_ratio_d0": 0.12, "consec_net_days": 3, "winner_rate": 55.0,
        "winner_delta_5d": 3.0, "cost_5pct": 9.8, "weight_avg": 10.3,
        "resistance_price": 11.0, "resistance_dist": 0.05,
        "support_price": 10.1, "support_dist": -0.06,
        "hype_tags": '["增持", "并购重组"]', "risk_flags": "[]",
        "hype_count": 2, "risk_flag_count": 0, "composite": 77.5, "rank": 1,
        "is_new": 0, "is_st": 0}])
    tags = pd.DataFrame([{"ts_code": "000001.SZ", "tag": "底部放量"}])
    p = report.render_full_report("20260918", _out(snap, tags=tags))
    text = p.read_text(encoding="utf-8")
    assert "000001.SZ" in text and "增持" in text and "底部放量" in text
    assert "不构成投资建议" in text


def test_pattern_report_zero_hits(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "SURGE_REPORTS_DIR", tmp_path)
    snap = pd.DataFrame([{"ts_code": "000001.SZ", "name": "测试", "pct_chg": 8.4,
                          "hype_tags": "[]", "risk_flags": "[]"}])
    p = report.render_pattern_report("20260918", _out(snap))
    assert p.exists() and "无形态命中" in p.read_text(encoding="utf-8")


def test_pattern_report_hits(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "SURGE_REPORTS_DIR", tmp_path)
    snap = pd.DataFrame([{
        "trade_date": "20260918", "ts_code": "000001.SZ", "name": "测试",
        "pct_chg": 8.4, "hype_count": 1, "risk_flag_count": 0,
        "composite": 80.0, "hype_tags": '["并购重组"]', "risk_flags": "[]"}])
    pat = pd.DataFrame([{
        "trade_date": "20260918", "ts_code": "000001.SZ",
        "boom_date": "20260901", "boom_pct": 7.2, "boom_vol_ratio": 2.9,
        "pullback_start": "20260902", "pullback_end": "20260917",
        "pullback_depth": 0.05, "vol_decay": 0.65,
        "plateau_high": 10.5, "plateau_days": 20, "breakout_pct": 0.076}])
    p = report.render_pattern_report("20260918", _out(snap, pat=pat))
    text = p.read_text(encoding="utf-8")
    assert "1 只" in text and "路径①" in text and "路径②" in text
    assert "10.50" in text


def test_full_report_nan_row_renders(tmp_path, monkeypatch):
    """健壮性审查C2: consec_net_days/industry 为 NaN 不崩溃不泄漏 'nan'."""
    import math
    monkeypatch.setattr(report, "SURGE_REPORTS_DIR", tmp_path)
    snap = pd.DataFrame([{
        "ts_code": "920298.BJ", "name": "北交所股", "industry": math.nan,
        "pct_chg": 30.0, "dist_ma60": 0.1, "dist_ma250": 0.2,
        "dd_high_250": -0.1, "elg_net_d0": math.nan, "lg_net_5d": math.nan,
        "net_ratio_d0": math.nan, "consec_net_days": math.nan,
        "winner_rate": math.nan, "winner_delta_5d": math.nan,
        "cost_5pct": math.nan, "weight_avg": math.nan,
        "resistance_price": math.nan, "resistance_dist": math.nan,
        "support_price": math.nan, "support_dist": math.nan,
        "hype_tags": "[]", "risk_flags": "[]",
        "hype_count": math.nan, "risk_flag_count": math.nan,
        "composite": 40.0, "rank": 1, "is_new": 1, "is_st": 0}])
    p = report.render_full_report("20260918", _out(snap))
    text = p.read_text(encoding="utf-8")
    assert "920298.BJ" in text
    assert "nan" not in text  # 无 NaN 泄漏文本
    assert "—" in text
