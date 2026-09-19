"""hype/risk 标签判据测试(合成数据帧,不触库;查询函数在 db 测试覆盖)."""

from __future__ import annotations

import pandas as pd

from davis_analyzer.systems.surge.factors import classify_hype_risk


def _args(**over):
    corp = pd.DataFrame([{"ts_code": "X", "ann_date": "20260901",
                          "event_type": "holder_trade", "direction": "positive"}])
    major = pd.DataFrame(columns=["ts_code", "ann_date", "event_type", "title"])
    ind = pd.DataFrame([{"index_code": "801150.SI", "ret20": 0.02, "ret60": 0.05,
                         "pct_rank60": 0.9, "pos_250": 0.5}]).iloc[0]
    base = dict(corp_events=corp, major_events=major, pledge_ratio=None,
                fin_consecutive_loss=False, is_st=False, industry_row=ind,
                vol_price_ok=True, research_count=5, day="20260918")
    base.update(over)
    return base


def test_hype_holder_increase_and_industry():
    hype, risk = classify_hype_risk(**_args())
    assert "增持" in hype
    assert "行业动量强" in hype  # pct_rank60=0.9 >= 0.70
    assert "量价齐升" in hype
    assert "研报覆盖热" in hype
    assert risk == []


def test_hype_repurchase_and_ma_events():
    corp = pd.DataFrame([{"ts_code": "X", "ann_date": "20260801",
                          "event_type": "repurchase", "direction": "positive"}])
    major = pd.DataFrame([{"ts_code": "X", "ann_date": "20260801",
                           "event_type": "ma", "title": "重组报告书"},
                          {"ts_code": "X", "ann_date": "20260802",
                           "event_type": "divest", "title": "资产出售"}])
    hype, _ = classify_hype_risk(**_args(corp_events=corp, major_events=major))
    assert "回购" in hype and "并购重组" in hype and "转型线索" in hype


def test_risk_reduce_loss_st_pledge():
    corp = pd.DataFrame([
        {"ts_code": "X", "ann_date": "20260901", "event_type": "holder_trade",
         "direction": "negative"},
        {"ts_code": "X", "ann_date": "20260901", "event_type": "share_float",
         "direction": "neutral"},
    ])
    _, risk = classify_hype_risk(**_args(
        corp_events=corp, fin_consecutive_loss=True, is_st=True,
        pledge_ratio=60.0))
    assert "减持" in risk and "解禁" in risk
    assert "持续亏损" in risk and "ST" in risk and "质押率高" in risk


def test_risk_major_events():
    major = pd.DataFrame([
        {"ts_code": "X", "ann_date": "20260801", "event_type": "refinance",
         "title": "定增"},
        {"ts_code": "X", "ann_date": "20260802", "event_type": "distress",
         "title": "立案"},
        {"ts_code": "X", "ann_date": "20260803", "event_type": "ma_halt",
         "title": "终止"},
    ])
    _, risk = classify_hype_risk(**_args(major_events=major))
    assert {"定增", "爆雷监管", "重组终止"} <= set(risk)


def test_window_filters_old_events():
    corp = pd.DataFrame([{"ts_code": "X", "ann_date": "20250101",
                          "event_type": "holder_trade", "direction": "positive"}])
    hype, _ = classify_hype_risk(**_args(corp_events=corp))
    assert "增持" not in hype  # 窗口外(>90自然日)


def test_industry_bottom_turn():
    ind = pd.DataFrame([{"index_code": "801150.SI", "ret20": 0.01, "ret60": -0.1,
                         "pct_rank60": 0.1, "pos_250": 0.15}]).iloc[0]
    hype, risk = classify_hype_risk(**_args(industry_row=ind))
    assert "行业底部拐点" in hype
    assert "行业动量强" not in hype
    assert "行业下行" not in hype  # ret20>0 不满足下行


def test_industry_down_and_cycle_top():
    ind = pd.DataFrame([{"index_code": "801150.SI", "ret20": -0.01, "ret60": -0.2,
                         "pct_rank60": 0.1, "pos_250": 0.5}]).iloc[0]
    _, risk = classify_hype_risk(**_args(industry_row=ind))
    assert "行业下行" in risk
    ind2 = pd.DataFrame([{"index_code": "801150.SI", "ret20": -0.01, "ret60": 0.02,
                          "pct_rank60": 0.85, "pos_250": 0.9}]).iloc[0]
    _, risk2 = classify_hype_risk(**_args(industry_row=ind2))
    assert "周期顶部" in risk2
    assert "行业动量强" not in risk2  # rank 0.85>=0.7 但不冲突——rank>=0.7 即动量强
    # 注: 周期顶部(高位+回落)与动量强可并存,判据独立


def test_industry_none_no_labels():
    hype, risk = classify_hype_risk(**_args(industry_row=None, vol_price_ok=False))
    assert "行业动量强" not in hype and "行业下行" not in risk


from davis_analyzer.systems.surge.factors import check_consecutive_loss  # noqa: E402


def test_consecutive_loss_true():
    rows = [("20241231", {"n_income": -100.0}),
            ("20251231", {"n_income": -200.0}),
            ("20260630", {"n_income": -50.0})]
    assert check_consecutive_loss(rows) is True


def test_consecutive_loss_false_when_latest_positive():
    rows = [("20241231", {"n_income": -100.0}),
            ("20251231", {"n_income": -200.0}),
            ("20260630", {"n_income": 30.0})]
    assert check_consecutive_loss(rows) is False


def test_consecutive_loss_insufficient_data():
    assert check_consecutive_loss([]) is False
    assert check_consecutive_loss([("20251231", {"n_income": -1.0})]) is False
    # 年报不足两期
    assert check_consecutive_loss([
        ("20260630", {"n_income": -1.0}),
        ("20251231", {"n_income": -1.0})]) is False
