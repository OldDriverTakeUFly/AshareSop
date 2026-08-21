"""Tests for stock universe delisted/suspended filtering.

背景 (2026-08-19): get_stock_list 自 2026-08-02 起为回测反幸存者偏差包含
D/P 状态股票, 而 build_stock_universe 未按 list_status 过滤, 导致 339 只退市
股进入实盘选股宇宙——紫天退(退) 以 composite=99.4 占据 top20 榜首。实盘
宇宙必须 active_only 过滤; 回测场景显式传 active_only=False 保留退市股。
"""

import pandas as pd

from davis_analyzer.stock_universe import build_stock_universe


class _FakeClient:
    def __init__(self, rows: list[dict]):
        self._df = pd.DataFrame(rows)

    def get_stock_list(self) -> pd.DataFrame:
        return self._df.copy()


_ROWS = [
    {"ts_code": "600000.SH", "name": "浦发银行", "industry": "银行", "list_status": "L"},
    {"ts_code": "688002.SH", "name": "睿创微纳", "industry": "军工电子", "list_status": "L"},
    # 退市（D）——曾经入选 top20 的三只
    {"ts_code": "300280.SZ", "name": "紫天退(退)", "industry": "传媒", "list_status": "D"},
    {"ts_code": "600387.SH", "name": "退海越(退)", "industry": "石化", "list_status": "D"},
    {"ts_code": "600766.SH", "name": "退园城(退)", "industry": "地产", "list_status": "D"},
    # 停牌（P）
    {"ts_code": "600999.SH", "name": "停牌股", "industry": "综合", "list_status": "P"},
    # 退市整理期: list_status 仍为 L, 名称带"退"标记
    {"ts_code": "002999.SZ", "name": "某股退", "industry": "电子", "list_status": "L"},
    # ST 家族（既有排除逻辑）
    {"ts_code": "600001.SH", "name": "ST宏盛", "industry": "贸易", "list_status": "L"},
    {"ts_code": "600002.SH", "name": "*ST新亿", "industry": "贸易", "list_status": "L"},
    # 北交所（2026-08-21 整体剔除：行情降级源无报价，NAV 0 计价/卖出顺延）
    {"ts_code": "920107.BJ", "name": "N恒兴", "industry": "电子", "list_status": "L"},
    {"ts_code": "920065.BJ", "name": "千岸科技", "industry": "机械", "list_status": "L"},
    {"ts_code": "835185.BJ", "name": "老三板股", "industry": "综合", "list_status": "L"},
]


class TestBuildStockUniverseActiveOnly:
    def test_default_drops_delisted_and_suspended(self):
        client = _FakeClient(_ROWS)
        stocks = build_stock_universe(client)
        codes = {s.ts_code for s in stocks}
        assert codes == {"600000.SH", "688002.SH"}

    def test_default_drops_beijing_exchange(self):
        """北交所（.BJ 后缀，含老 83 系）整体剔除——行情源缺口无法定价."""
        client = _FakeClient(_ROWS)
        codes = {s.ts_code for s in build_stock_universe(client)}
        assert "920107.BJ" not in codes
        assert "920065.BJ" not in codes
        assert "835185.BJ" not in codes

    def test_active_only_false_still_drops_beijing(self):
        """回测场景保留退市股，但北交所同样剔除（政策性整体排除）."""
        client = _FakeClient(_ROWS)
        codes = {s.ts_code for s in build_stock_universe(client, active_only=False)}
        assert "300280.SZ" in codes  # D 保留
        assert "920107.BJ" not in codes  # BJ 仍剔除
        assert "835185.BJ" not in codes

    def test_default_drops_delisting_period_by_name(self):
        """整理期股票 list_status=L 但名称含"退" → 名称兜底剔除."""
        client = _FakeClient(_ROWS)
        codes = {s.ts_code for s in build_stock_universe(client)}
        assert "002999.SZ" not in codes

    def test_default_still_excludes_st(self):
        client = _FakeClient(_ROWS)
        codes = {s.ts_code for s in build_stock_universe(client)}
        assert "600001.SH" not in codes
        assert "600002.SH" not in codes

    def test_active_only_false_keeps_delisted_for_backtest(self):
        """回测反幸存者偏差: active_only=False 保留 D/P 与整理期, 但 ST 仍排除."""
        client = _FakeClient(_ROWS)
        codes = {s.ts_code for s in build_stock_universe(client, active_only=False)}
        assert "300280.SZ" in codes  # D 保留
        assert "600999.SH" in codes  # P 保留
        assert "002999.SZ" in codes  # 整理期保留
        assert "600001.SH" not in codes  # ST 仍排除（既有语义不变）

    def test_missing_list_status_column_falls_back_to_name_filter(self):
        rows = [
            {"ts_code": "600000.SH", "name": "浦发银行", "industry": "银行"},
            {"ts_code": "300280.SZ", "name": "紫天退(退)", "industry": "传媒"},
        ]
        client = _FakeClient(rows)
        codes = {s.ts_code for s in build_stock_universe(client)}
        assert codes == {"600000.SH"}

    def test_empty_list_returns_empty(self):
        client = _FakeClient([])
        assert build_stock_universe(client) == []
