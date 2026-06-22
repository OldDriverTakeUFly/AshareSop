import pandas as pd

import stockhot.risk_alert as ra


def _make_st_df():
    return pd.DataFrame(
        {
            "代码": ["000001", "000002"],
            "名称": ["ST平安", "ST万科"],
            "最新价": [10.5, 3.2],
            "涨跌幅": [-2.5, -5.0],
        }
    )


def test_fetch_st_stocks_with_mock(monkeypatch):
    monkeypatch.setattr(ra.ak, "stock_zh_a_st_em", _make_st_df)

    result = ra.fetch_st_stocks()

    assert len(result) == 2
    assert result[0]["代码"] == "000001"
    assert result[0]["名称"] == "ST平安"
    assert result[0]["最新价"] == 10.5
    assert result[0]["涨跌幅"] == -2.5


def test_detect_abnormal_volatility_filters_reasons():
    lhb = [
        {"代码": "000001", "名称": "测试A", "reason": "日涨幅偏离值达7%"},
        {"代码": "000002", "名称": "测试B", "reason": "日换手率达到20%"},
        {"代码": "000003", "名称": "测试C", "reason": "连续三个交易日跌幅偏离值累计达20%"},
    ]

    result = ra.detect_abnormal_volatility(lhb)

    assert len(result) == 3
    assert all(item["代码"] in {"000001", "000002", "000003"} for item in result)


def test_detect_abnormal_volatility_no_match():
    lhb = [
        {"代码": "000001", "名称": "测试A", "reason": "涨幅偏离值达15%"},
        {"代码": "000002", "名称": "测试B", "reason": "其他原因"},
    ]

    result = ra.detect_abnormal_volatility(lhb)

    assert result == []


def test_detect_capital_flight_flags_outflows():
    sectors = [
        {"name": "半导体", "main_net": 50.0},
        {"name": "房地产", "main_net": -30.5},
        {"name": "银行", "main_net": 10.0},
        {"name": "传媒", "main_net": -5.2},
    ]

    result = ra.detect_capital_flight(sectors)

    assert len(result) == 2
    assert result[0]["name"] == "房地产"
    assert result[1]["name"] == "传媒"


def test_detect_high_position_risks_flags_consecutive():
    pool = [
        {"代码": "000001", "名称": "股票A", "consecutive_boards": 5},
        {"代码": "000002", "名称": "股票B", "consecutive_boards": 3},
        {"代码": "000003", "名称": "股票C", "consecutive_boards": 2},
        {"代码": "000004", "名称": "股票D", "consecutive_boards": 4},
    ]

    result = ra.detect_high_position_risks(pool)

    assert len(result) == 2
    codes = {item["代码"] for item in result}
    assert codes == {"000001", "000004"}


def test_generate_summary():
    st_stocks = [{"代码": "000001"}]
    abnormal = [{"代码": "000002"}, {"代码": "000003"}]
    flight = [{"name": "房地产"}]
    high_pos = [{"代码": "000004"}]

    summary = ra.generate_summary(st_stocks, abnormal, flight, high_pos)

    assert "风险提示" in summary
    assert "ST股票: 1 只" in summary
    assert "异常波动: 2 只" in summary
    assert "资金出逃板块: 1 个" in summary
    assert "高位连板风险: 1 只" in summary


def test_run_risk_alert_analysis_full(monkeypatch):
    monkeypatch.setattr(ra.ak, "stock_zh_a_st_em", _make_st_df)
    monkeypatch.setattr(
        ra.ak, "stock_zh_a_stop_em", lambda: pd.DataFrame({"代码": ["600000"], "名称": ["ST浦发"]})
    )

    market_data = {
        "date": "2026-04-17",
        "dragon_tiger_detail": [
            {"代码": "000001", "名称": "测试A", "reason": "日涨幅偏离值达7%"},
        ],
        "fund_flow_sector": [
            {"name": "房地产", "main_net": -30.5},
        ],
        "limit_up_pool": [
            {"代码": "000010", "名称": "连板股", "consecutive_boards": 5},
        ],
    }
    monkeypatch.setattr(ra, "get_daily_data", lambda date: market_data)

    saved_daily = []
    saved_analysis = []
    monkeypatch.setattr(ra, "save_daily_data", lambda data: saved_daily.append(data))
    monkeypatch.setattr(
        ra,
        "save_analysis_result",
        lambda date, kind, payload: saved_analysis.append((date, kind, payload)),
    )

    result = ra.run_risk_alert_analysis("2026-04-17")

    assert result["date"] == "2026-04-17"
    assert result["status"] == "success"
    assert len(result["data"]["st_stocks"]) == 2
    assert len(result["data"]["abnormal_volatility"]) == 1
    assert len(result["data"]["capital_flight"]) == 1
    assert len(result["data"]["high_position_risks"]) == 1
    assert "风险提示" in result["data"]["summary"]
    assert saved_analysis[0][1] == "risk_alert"


def test_empty_data_no_false_positives():
    assert ra.detect_abnormal_volatility([]) == []
    assert ra.detect_abnormal_volatility([{"代码": "000001"}]) == []

    assert ra.detect_capital_flight([]) == []
    assert ra.detect_capital_flight([{"name": "银行", "main_net": 10.0}]) == []

    assert ra.detect_high_position_risks([]) == []
    assert ra.detect_high_position_risks([{"代码": "000001", "consecutive_boards": 2}]) == []

    summary = ra.generate_summary([], [], [], [])
    assert "未检出显著风险信号" in summary
