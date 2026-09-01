import pandas as pd

import stockhot.dragon_tiger as dt

# Tushare top_list 格式（2026-07-07 起 fetch_lhb_detail 走 Tushare 优先）
_DETAIL_DF_TS = pd.DataFrame(
    {
        "ts_code": ["000001.SZ", "600519.SH"],
        "name": ["平安银行", "贵州茅台"],
        "reason": ["涨幅偏离", "换手率达标的证券"],
        "close": [12.50, 1680.00],
        "pct_change": [10.01, -5.23],
        "net_amount": [5000000.0, -3000000.0],
        "l_buy": [8000000.0, 2000000.0],
        "l_sell": [3000000.0, 5000000.0],
        "trade_date": ["20260512", "20260512"],
    }
)

# Tushare top_inst 格式（exile 列为机构名称）
_INST_DF_TS = pd.DataFrame(
    {
        "ts_code": ["INST001", "INST002"],
        "exile": ["机构A", "机构B"],
        "buy": [10000000.0, 5000000.0],
        "sell": [3000000.0, 8000000.0],
        "net": [7000000.0, -3000000.0],
    }
)

_DETAIL_DF = pd.DataFrame(
    {
        "代码": ["000001", "600519"],
        "名称": ["平安银行", "贵州茅台"],
        "上榜原因": ["涨幅偏离", "换手率达标的证券"],
        "收盘价": [12.50, 1680.00],
        "涨跌幅": [10.01, -5.23],
        "龙虎榜净买额": [5000000.0, -3000000.0],
        "龙虎榜买入额": [8000000.0, 2000000.0],
        "龙虎榜卖出额": [3000000.0, 5000000.0],
        "上榜日": ["20260512", "20260512"],
    }
)

_INST_DF = pd.DataFrame(
    {
        "代码": ["INST001", "INST002"],
        "名称": ["机构A", "机构B"],
        "机构买入总额": [10000000.0, 5000000.0],
        "机构卖出总额": [3000000.0, 8000000.0],
        "机构买入净额": [7000000.0, -3000000.0],
    }
)

_BROKER_DF = pd.DataFrame(
    {
        "营业部名称": ["中信证券上海分公司", "国泰君安深圳分公司"],
        "买入总金额": [6000000.0, 2000000.0],
        "卖出总金额": [1000000.0, 4000000.0],
        "总买卖净额": [5000000.0, -2000000.0],
    }
)


def _patch_tushare(monkeypatch, responder):
    """打桩 Tushare 数据源（fetch_lhb_detail / fetch_institutional_trading 自
    2026-07-07 起 Tushare top_list / top_inst 优先，AKShare 仅兜底）.

    responder: callable(endpoint, **kw) -> DataFrame | None
    """
    # 类方法 patch 会注入 self, 包一层适配保持 responder(endpoint, **kw) 契约
    monkeypatch.setattr(
        "stockhot.data_layer.tushare_gateway.TushareGateway.call",
        lambda self, api_name, *args, **kw: responder(api_name, *args, **kw),
    )


def test_fetch_lhb_detail_with_mock(monkeypatch):
    _patch_tushare(monkeypatch, lambda endpoint, **kw: _DETAIL_DF_TS)

    result = dt.fetch_lhb_detail("2026-05-12", "2026-05-12")

    assert len(result) == 2
    assert result[0]["code"] == "000001.SZ"
    assert result[0]["name"] == "平安银行"
    assert result[0]["change_pct"] == 10.01
    assert result[1]["code"] == "600519.SH"


def test_fetch_lhb_detail_tushare_down_falls_back_to_akshare(monkeypatch):
    """Tushare 失败时走 AKShare fallback，中文字段映射不回退."""
    _patch_tushare(monkeypatch, lambda endpoint, **kw: None)
    monkeypatch.setattr(dt, "safe_akshare_call", lambda fn, **kw: _DETAIL_DF)

    result = dt.fetch_lhb_detail("2026-05-12", "2026-05-12")

    assert len(result) == 2
    assert result[0]["code"] == "000001"


def test_fetch_lhb_detail_empty(monkeypatch):
    _patch_tushare(monkeypatch, lambda endpoint, **kw: None)
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

    def _ts_responder(endpoint, **kw):
        if endpoint == "top_list":
            return _DETAIL_DF_TS
        if endpoint == "top_inst":
            return _INST_DF_TS
        return None

    _patch_tushare(monkeypatch, _ts_responder)
    # brokers（营业部）仍走 AKShare stock_lhb_hyyyb_em
    monkeypatch.setattr(dt, "safe_akshare_call", lambda fn, **kw: _BROKER_DF)
    monkeypatch.setattr(dt, "save_daily_data", lambda d: saved_daily.update(d))
    monkeypatch.setattr(
        dt,
        "save_analysis_result",
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
    _patch_tushare(monkeypatch, lambda endpoint, **kw: None)
    monkeypatch.setattr(dt, "safe_akshare_call", lambda fn, **kw: pd.DataFrame())
    monkeypatch.setattr(
        dt.ak,
        "stock_lhb_detail_em",
        lambda **kw: pd.DataFrame(),
    )
    monkeypatch.setattr(
        dt.ak,
        "stock_lhb_jgmmtj_em",
        lambda **kw: pd.DataFrame(),
    )
    monkeypatch.setattr(
        dt.ak,
        "stock_lhb_hyyyb_em",
        lambda **kw: pd.DataFrame(),
    )

    result = dt.run_dragon_tiger_analysis("2026-05-10")

    assert result["date"] == "2026-05-10"
    assert result["status"] == "no_data"
    assert result["data"] == {}


# Tushare top_inst 缺 exile/net 列的现实形态(2026-09-01 实测缺失)
_INST_DF_TS_SPARSE = pd.DataFrame(
    {
        "ts_code": ["000560.SZ", "002084.SZ"],
        "buy": [30000000.0, 12000000.0],
        "sell": [40000000.0, 5000000.0],
    }
)


def test_fetch_institutional_fills_missing_name_and_net(monkeypatch):
    """缺 exile/net 列时:inst_name 兜底『机构专用』,net=buy-sell。"""
    seen = {}

    class FakeGateway:
        def call(self, api, **kw):
            seen["api"] = api
            return _INST_DF_TS_SPARSE.copy()

    # fetch_institutional_trading 内部是 `from stockhot.data_layer import get_gateway`
    # 的函数级局部导入——patch 模块属性即可在调用时生效
    import stockhot.data_layer
    monkeypatch.setattr(stockhot.data_layer, "get_gateway", lambda: FakeGateway())

    rows = dt.fetch_institutional_trading("2026-09-01", "2026-09-01")
    assert seen["api"] == "top_inst"
    assert rows and rows[0]["inst_name"] == "机构专用"
    assert rows[0]["net_amount"] == -10000000.0
    assert rows[1]["net_amount"] == 7000000.0
