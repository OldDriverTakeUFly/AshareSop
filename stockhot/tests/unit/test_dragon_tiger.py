import pandas as pd

import stockhot.dragon_tiger as dt


_DETAIL_DF = pd.DataFrame({
    "代码": ["000001", "600519"],
    "名称": ["平安银行", "贵州茅台"],
    "上榜原因": ["涨幅偏离", "换手率达标的证券"],
    "收盘价": [12.50, 1680.00],
    "涨跌幅": [10.01, -5.23],
    "龙虎榜净买额": [5000000.0, -3000000.0],
    "龙虎榜买入额": [8000000.0, 2000000.0],
    "龙虎榜卖出额": [3000000.0, 5000000.0],
    "上榜日期": ["20260512", "20260512"],
})

_INST_DF = pd.DataFrame({
    "机构代码": ["INST001", "INST002"],
    "机构名称": ["机构A", "机构B"],
    "买入额": [10000000.0, 5000000.0],
    "卖出额": [3000000.0, 8000000.0],
    "净额": [7000000.0, -3000000.0],
})

_BROKER_DF = pd.DataFrame({
    "营业部名称": ["中信证券上海分公司", "国泰君安深圳分公司"],
    "买入额": [6000000.0, 2000000.0],
    "卖出额": [1000000.0, 4000000.0],
    "净额": [5000000.0, -2000000.0],
})


def test_fetch_lhb_detail_with_mock(monkeypatch):
    monkeypatch.setattr(dt, "safe_akshare_call", lambda fn, **kw: _DETAIL_DF)

    result = dt.fetch_lhb_detail("2026-05-12", "2026-05-12")

    assert len(result) == 2
    assert result[0]["code"] == "000001"
    assert result[0]["name"] == "平安银行"
    assert result[0]["change_pct"] == 10.01
    assert result[1]["code"] == "600519"


def test_fetch_lhb_detail_empty(monkeypatch):
    monkeypatch.setattr(dt, "safe_akshare_call", lambda fn, **kw: pd.DataFrame())

    result = dt.fetch_lhb_detail("2026-05-12", "2026-05-12")

    assert result == []


def test_analyze_hot_money_tracking():
    detail = [
        {"code": "000001", "name": "平安银行", "net_buy_amount": 5000000.0},
        {"code": "600519", "name": "贵州茅台", "net_buy_amount": -3000000.0},
    ]
    brokers = [
        {"broker_name": "中信证券上海分公司", "net_amount": 5000000.0},
        {"broker_name": "国泰君安深圳分公司", "net_amount": -2000000.0},
    ]

    result = dt.analyze_hot_money_tracking(detail, brokers)

    assert len(result) == 2
    assert result[0]["broker"] == "中信证券上海分公司"
    assert result[0]["net_direction"] == "net_buy"
    assert result[1]["broker"] == "国泰君安深圳分公司"
    assert result[1]["net_direction"] == "net_sell"


def test_track_institutional_seats():
    inst = [
        {"inst_code": "INST001", "inst_name": "机构A", "net_amount": 7000000.0},
        {"inst_code": "INST002", "inst_name": "机构B", "net_amount": -3000000.0},
    ]

    result = dt.track_institutional_seats(inst)

    assert len(result) == 2
    assert result[0]["inst_name"] == "机构A"
    assert result[0]["net_amount"] == 7000000.0
    assert result[1]["inst_name"] == "机构B"


def test_generate_summary():
    detail = [{"net_buy_amount": 5000000.0}, {"net_buy_amount": -3000000.0}]
    inst = [
        {"net_amount": 7000000.0, "buy_amount": 10000000.0, "sell_amount": 3000000.0},
        {"net_amount": -3000000.0, "buy_amount": 5000000.0, "sell_amount": 8000000.0},
    ]
    brokers = [{"net_amount": 5000000.0}, {"net_amount": -2000000.0}]
    hot_money = [{"broker": "中信证券"}]

    result = dt.generate_summary(detail, inst, brokers, hot_money)

    assert "龙虎榜上榜股票数: 2" in result
    assert "机构席位数: 2" in result
    assert "活跃营业部数: 2" in result
    assert "游资追踪记录数: 1" in result


def test_run_dragon_tiger_analysis_full(monkeypatch):
    saved_daily = {}
    saved_analysis = []

    _call_count = {"n": 0}
    _responses = [_DETAIL_DF, _INST_DF, _BROKER_DF]

    def _mock_safe_call(fn, **kw):
        idx = _call_count["n"]
        _call_count["n"] += 1
        return _responses[idx] if idx < len(_responses) else pd.DataFrame()

    monkeypatch.setattr(dt, "safe_akshare_call", _mock_safe_call)
    monkeypatch.setattr(dt, "save_daily_data", lambda d: saved_daily.update(d))
    monkeypatch.setattr(
        dt, "save_analysis_result",
        lambda date, kind, payload: saved_analysis.append((date, kind)),
    )

    result = dt.run_dragon_tiger_analysis("2026-05-12")

    assert result["date"] == "2026-05-12"
    assert result["status"] == "success"
    assert len(result["data"]["detail"]) == 2
    assert len(result["data"]["institutional"]) == 2
    assert len(result["data"]["brokers"]) == 2
    assert "summary" in result["data"]
    assert saved_daily.get("date") == "2026-05-12"
    assert ("2026-05-12", "dragon_tiger") in saved_analysis


def test_non_trading_day_graceful(monkeypatch):
    monkeypatch.setattr(dt, "safe_akshare_call", lambda fn, **kw: pd.DataFrame())
    monkeypatch.setattr(
        dt.ak, "stock_lhb_detail_em",
        lambda **kw: pd.DataFrame(),
    )
    monkeypatch.setattr(
        dt.ak, "stock_lhb_jgmmtj_em",
        lambda **kw: pd.DataFrame(),
    )
    monkeypatch.setattr(
        dt.ak, "stock_lhb_hyyyb_em",
        lambda **kw: pd.DataFrame(),
    )

    result = dt.run_dragon_tiger_analysis("2026-05-10")

    assert result["date"] == "2026-05-10"
    assert result["status"] == "no_data"
    assert result["data"] == {}
