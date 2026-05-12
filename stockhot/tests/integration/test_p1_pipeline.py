"""Integration tests for P1 market analysis modules."""

import pytest
import pandas as pd

def make_limit_up_df():
    return pd.DataFrame({
        "代码": ["000001", "000002", "600123", "300456"],
        "名称": ["平安银行", "万科A", "兰花科创", "南方轴承"],
        "涨跌幅": [10.0, 10.0, 10.0, 10.0],
        "封板资金": [5000000, 3000000, 8000000, 2000000],
        "最高板": [2, 1, 4, 1],
        "连板数": [2, 1, 4, 1],
        "所属行业": ["银行", "房地产", "煤炭", "机械"],
        "炸板次数": [0, 1, 0, 2],
        "首次封板时间": ["09:30", "10:00", "09:25", "14:00"],
        "最后封板时间": ["14:55", "14:30", "15:00", "14:50"],
        "换手率": [5.2, 8.1, 3.5, 12.0],
    })


def make_broken_df():
    return pd.DataFrame({
        "代码": ["600789"],
        "名称": ["某科技"],
        "涨跌幅": [5.2],
        "炸板次数": [3],
        "所属行业": ["电子"],
    })


def make_limit_down_df():
    return pd.DataFrame({
        "代码": ["002345"],
        "名称": ["某医药"],
        "涨跌幅": [-10.0],
        "所属行业": ["医药"],
    })


def make_lhb_df():
    return pd.DataFrame({
        "代码": ["000001"],
        "名称": ["平安银行"],
        "上榜原因": ["日涨幅偏离值达7%"],
        "收盘价": [15.5],
        "涨跌幅": [8.5],
        "龙虎榜净买额": [1000000],
        "龙虎榜买入额": [3000000],
        "龙虎榜卖出额": [2000000],
        "上榜日期": ["20260424"],
    })


def make_institutional_df():
    return pd.DataFrame({
        "机构代码": ["I001"],
        "机构名称": ["机构A"],
        "买入额": [5000000],
        "卖出额": [2000000],
        "净额": [3000000],
    })


def make_broker_df():
    return pd.DataFrame({
        "营业部名称": ["中信证券总部"],
        "买入额": [8000000],
        "卖出额": [1000000],
        "净额": [7000000],
    })


def make_market_fund_flow_df():
    return pd.DataFrame({
        "日期": ["20260424", "20260423", "20260422"],
        "主力净流入-净额": [-500000, -300000, 200000],
        "主力净流入-净流入占比": [-0.5, -0.3, 0.2],
        "超大单净流入-净额": [-200000, -100000, 100000],
        "大单净流入-净额": [-300000, -200000, 100000],
        "中单净流入-净额": [100000, 50000, -50000],
        "小单净流入-净额": [400000, 250000, -150000],
    })


def make_sector_fund_flow_df():
    return pd.DataFrame({
        "名称": ["银行", "房地产", "煤炭"],
        "今日涨跌幅": [1.5, -0.5, 2.0],
        "主力净流入-净额": [500000, -300000, 800000],
        "主力净流入-净流入占比": [0.5, -0.3, 0.8],
        "超大单净流入-净额": [200000, -100000, 300000],
        "大单净流入-净额": [300000, -200000, 500000],
        "中单净流入-净额": [100000, 50000, -200000],
        "小单净流入-净额": [-600000, 250000, -100000],
    })


def make_st_df():
    return pd.DataFrame({
        "代码": ["000001"],
        "名称": ["ST某某"],
        "最新价": [3.5],
        "涨跌幅": [-5.0],
    })


class TestLimitUpIntegration:
    def test_full_pipeline_with_mock(self, monkeypatch, tmp_path):
        monkeypatch.setattr("stockhot.limit_up.ak.stock_zt_pool_em", lambda date: make_limit_up_df())
        monkeypatch.setattr("stockhot.limit_up.ak.stock_zt_pool_zbgc_em", lambda date: make_broken_df())
        monkeypatch.setattr("stockhot.limit_up.ak.stock_zt_pool_dtgc_em", lambda date: make_limit_down_df())
        monkeypatch.setattr("stockhot.limit_up.save_daily_data", lambda data: None)
        monkeypatch.setattr("stockhot.limit_up.save_analysis_result", lambda *a, **kw: None)

        from stockhot.limit_up import run_limit_up_analysis
        result = run_limit_up_analysis("2026-04-24")

        assert result["status"] == "success"
        assert len(result["data"]["limit_up_pool"]) == 4
        assert len(result["data"]["broken_pool"]) == 1
        assert len(result["data"]["consecutive_boards"]) >= 1
        assert len(result["data"]["sector_correlation"]) >= 1


class TestDragonTigerIntegration:
    def test_full_pipeline_with_mock(self, monkeypatch):
        monkeypatch.setattr("stockhot.dragon_tiger.ak.stock_lhb_detail_em", lambda **kw: make_lhb_df())
        monkeypatch.setattr("stockhot.dragon_tiger.ak.stock_lhb_jgmmtj_em", lambda **kw: make_institutional_df())
        monkeypatch.setattr("stockhot.dragon_tiger.ak.stock_lhb_hyyyb_em", lambda **kw: make_broker_df())
        monkeypatch.setattr("stockhot.dragon_tiger.save_daily_data", lambda data: None)
        monkeypatch.setattr("stockhot.dragon_tiger.save_analysis_result", lambda *a, **kw: None)

        from stockhot.dragon_tiger import run_dragon_tiger_analysis
        result = run_dragon_tiger_analysis("2026-04-24")

        assert result["status"] == "success"
        assert len(result["data"]["detail"]) >= 1


class TestFundFlowIntegration:
    def test_full_pipeline_with_mock(self, monkeypatch):
        monkeypatch.setattr("stockhot.fund_flow.ak.stock_market_fund_flow", lambda: make_market_fund_flow_df())
        monkeypatch.setattr("stockhot.fund_flow.ak.stock_sector_fund_flow_rank", lambda **kw: make_sector_fund_flow_df())
        monkeypatch.setattr("stockhot.fund_flow.save_daily_data", lambda data: None)
        monkeypatch.setattr("stockhot.fund_flow.save_analysis_result", lambda *a, **kw: None)

        from stockhot.fund_flow import run_fund_flow_analysis
        result = run_fund_flow_analysis("2026-04-24")

        assert result["status"] == "success"
        assert "trend" in result["data"]


class TestRiskAlertIntegration:
    def test_full_pipeline_with_mock(self, monkeypatch):
        monkeypatch.setattr("stockhot.risk_alert.ak.stock_zh_a_st_em", lambda: make_st_df())
        monkeypatch.setattr("stockhot.risk_alert.ak.stock_zh_a_stop_em", lambda: pd.DataFrame())
        monkeypatch.setattr("stockhot.risk_alert.save_daily_data", lambda data: None)
        monkeypatch.setattr("stockhot.risk_alert.save_analysis_result", lambda *a, **kw: None)
        monkeypatch.setattr("stockhot.risk_alert.get_daily_data", lambda *a, **kw: {"date": "2026-04-24"})

        from stockhot.risk_alert import run_risk_alert_analysis
        result = run_risk_alert_analysis("2026-04-24")

        assert result["status"] == "success"
        assert "st_stocks" in result["data"]


class TestNonTradingDay:
    def test_all_modules_handle_empty_gracefully(self, monkeypatch):
        """All P1 modules should return gracefully on non-trading days."""
        empty_df = pd.DataFrame()

        # Limit up
        monkeypatch.setattr("stockhot.limit_up.ak.stock_zt_pool_em", lambda date: empty_df)
        monkeypatch.setattr("stockhot.limit_up.ak.stock_zt_pool_zbgc_em", lambda date: empty_df)
        monkeypatch.setattr("stockhot.limit_up.ak.stock_zt_pool_dtgc_em", lambda date: empty_df)
        monkeypatch.setattr("stockhot.limit_up.save_daily_data", lambda data: None)
        monkeypatch.setattr("stockhot.limit_up.save_analysis_result", lambda *a, **kw: None)

        from stockhot.limit_up import run_limit_up_analysis
        result = run_limit_up_analysis("2026-01-01")
        assert result["status"] == "no_data"

        # Dragon tiger
        monkeypatch.setattr("stockhot.dragon_tiger.ak.stock_lhb_detail_em", lambda **kw: empty_df)
        monkeypatch.setattr("stockhot.dragon_tiger.ak.stock_lhb_jgmmtj_em", lambda **kw: empty_df)
        monkeypatch.setattr("stockhot.dragon_tiger.ak.stock_lhb_hyyyb_em", lambda **kw: empty_df)
        monkeypatch.setattr("stockhot.dragon_tiger.save_daily_data", lambda data: None)
        monkeypatch.setattr("stockhot.dragon_tiger.save_analysis_result", lambda *a, **kw: None)

        from stockhot.dragon_tiger import run_dragon_tiger_analysis
        result = run_dragon_tiger_analysis("2026-01-01")
        assert result["status"] == "no_data"
