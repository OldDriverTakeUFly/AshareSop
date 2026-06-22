import pandas as pd

import stockhot.fund_flow as ff


def _make_market_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


MARKET_DF_ROWS = [
    {
        "日期": "20260501",
        "主力净流入-净额": 1050000000.0,  # 10.5亿 in 元
        "主力净流入-净流入占比": 0.52,
        "超大单净流入-净额": 500000000.0,
        "大单净流入-净额": 550000000.0,
        "中单净流入-净额": -300000000.0,
        "小单净流入-净额": -750000000.0,
    },
    {
        "日期": "20260502",
        "主力净流入-净额": 1230000000.0,  # 12.3亿 in 元
        "主力净流入-净流入占比": 0.61,
        "超大单净流入-净额": 600000000.0,
        "大单净流入-净额": 630000000.0,
        "中单净流入-净额": -200000000.0,
        "小单净流入-净额": -1030000000.0,
    },
    {
        "日期": "20260503",
        "主力净流入-净额": 1500000000.0,  # 15.0亿 in 元
        "主力净流入-净流入占比": 0.75,
        "超大单净流入-净额": 800000000.0,
        "大单净流入-净额": 700000000.0,
        "中单净流入-净额": -100000000.0,
        "小单净流入-净额": -1400000000.0,
    },
]

SECTOR_DF_ROWS = [
    {
        "名称": "电子",
        "今日涨跌幅": 2.34,
        "主力净流入-净额": 2560000000.0,  # 25.6亿 in 元
        "主力净流入-净流入占比": 1.28,
        "超大单净流入-净额": 1200000000.0,
        "大单净流入-净额": 1360000000.0,
        "中单净流入-净额": -500000000.0,
        "小单净流入-净额": -2060000000.0,
    },
    {
        "名称": "通信",
        "今日涨跌幅": 1.87,
        "主力净流入-净额": 1820000000.0,  # 18.2亿 in 元
        "主力净流入-净流入占比": 0.91,
        "超大单净流入-净额": 900000000.0,
        "大单净流入-净额": 920000000.0,
        "中单净流入-净额": -300000000.0,
        "小单净流入-净额": -1520000000.0,
    },
]


def test_fetch_market_fund_flow_with_mock(monkeypatch):
    df = _make_market_df(MARKET_DF_ROWS)
    monkeypatch.setattr(ff, "safe_akshare_call", lambda fn, **kw: df)

    result = ff.fetch_market_fund_flow()

    assert len(result) == 3
    assert result[0]["date"] == "2026-05-01"
    assert result[0]["main_net"] == 10.5
    assert result[1]["date"] == "2026-05-02"
    assert result[2]["main_net"] == 15.0
    assert result[0]["main_pct"] == 0.52


def test_fetch_sector_fund_flow_with_mock(monkeypatch):
    df = _make_market_df(SECTOR_DF_ROWS)
    monkeypatch.setattr(ff, "safe_akshare_call", lambda fn, **kw: df)

    result = ff.fetch_sector_fund_flow()

    assert len(result) == 2
    assert result[0]["name"] == "电子"
    assert result[0]["main_net"] == 25.6
    assert result[0]["change_pct"] == 2.34
    assert result[1]["name"] == "通信"


def test_analyze_fund_flow_trend_inflow():
    market_flow = [
        {"main_net": 5.0, "huge_net": 2.0, "large_net": 3.0, "medium_net": -1.0, "small_net": -4.0},
        {"main_net": 8.0, "huge_net": 4.0, "large_net": 4.0, "medium_net": -2.0, "small_net": -6.0},
        {
            "main_net": 12.0,
            "huge_net": 6.0,
            "large_net": 6.0,
            "medium_net": -3.0,
            "small_net": -9.0,
        },
    ]

    result = ff.analyze_fund_flow_trend(market_flow, lookback=3)

    assert result["direction"] == "持续流入"
    assert result["momentum"] == "加速"
    assert result["lookback_rows"] == 3


def test_analyze_fund_flow_trend_outflow():
    market_flow = [
        {
            "main_net": -5.0,
            "huge_net": -2.0,
            "large_net": -3.0,
            "medium_net": 1.0,
            "small_net": 4.0,
        },
        {
            "main_net": -8.0,
            "huge_net": -4.0,
            "large_net": -4.0,
            "medium_net": 2.0,
            "small_net": 6.0,
        },
        {
            "main_net": -12.0,
            "huge_net": -6.0,
            "large_net": -6.0,
            "medium_net": 3.0,
            "small_net": 9.0,
        },
    ]

    result = ff.analyze_fund_flow_trend(market_flow, lookback=3)

    assert result["direction"] == "持续流出"
    assert result["momentum"] == "加速"
    assert result["large_vs_retail_divergence"] is True
    assert result["lookback_rows"] == 3


def test_generate_summary():
    market_flow = [
        {
            "main_net": 15.0,
            "huge_net": 8.0,
            "large_net": 7.0,
            "medium_net": -1.0,
            "small_net": -14.0,
        },
    ]
    sector_flow = [{"name": "电子", "main_net": 25.6}]
    trend = {"direction": "持续流入", "momentum": "加速", "large_vs_retail_divergence": True}

    result = ff.generate_summary(market_flow, sector_flow, trend)

    assert "净流入15.00亿" in result
    assert "持续流入" in result
    assert "加速" in result
    assert "背离" in result
    assert "电子" in result


def test_run_fund_flow_analysis_full(monkeypatch):
    market_df = _make_market_df(MARKET_DF_ROWS)
    sector_df = _make_market_df(SECTOR_DF_ROWS)

    call_count = {"n": 0}

    def fake_safe_call(fn, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return market_df
        return sector_df

    monkeypatch.setattr(ff, "safe_akshare_call", fake_safe_call)

    saved_daily = {}
    saved_analysis = {}

    monkeypatch.setattr(ff, "save_daily_data", lambda data: saved_daily.update(data))
    monkeypatch.setattr(
        ff,
        "save_analysis_result",
        lambda date, atype, result: saved_analysis.update(
            {"date": date, "type": atype, "result": result}
        ),
    )

    result = ff.run_fund_flow_analysis("2026-05-03")

    assert result["date"] == "2026-05-03"
    assert result["status"] == "success"
    assert "market_flow" in result["data"]
    assert "sector_flow" in result["data"]
    assert "trend" in result["data"]
    assert "summary" in result["data"]
    assert saved_daily["date"] == "2026-05-03"
    assert saved_analysis["type"] == "fund_flow_trend"


def test_empty_data_graceful(monkeypatch):
    monkeypatch.setattr(ff, "safe_akshare_call", lambda fn, **kw: pd.DataFrame())

    saved_daily = {}
    saved_analysis = {}

    monkeypatch.setattr(ff, "save_daily_data", lambda data: saved_daily.update(data))
    monkeypatch.setattr(
        ff, "save_analysis_result", lambda *a, **kw: saved_analysis.update({"called": True})
    )

    result = ff.run_fund_flow_analysis("2026-05-03")

    assert result["status"] == "no_data"
    assert result["data"] == {}
    assert not saved_daily
    assert not saved_analysis
