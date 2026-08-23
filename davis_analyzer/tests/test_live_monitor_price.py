"""live_monitor 实时价与交易日判定测试（2026-08-23 技术债修复）.

背景: get_realtime_price 原版盘中也读 DAL daily_price 缓存 close(=昨日价),
止损/止盈判定失真——已知债「不解决任何日内策略不能上线」。修复后盘中
优先 AKShare spot 快照, 失败/盘后回落 DAL; is_trading_day 判据放宽为
最近行情行距今 ≤5 自然日。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd

from davis_analyzer.paper_trading import live_monitor as lm


def _mock_repo(close: float | None = None):
    repo = MagicMock()
    if close is None:
        repo.get_daily_prices.return_value = pd.DataFrame()
    else:
        repo.get_daily_prices.return_value = pd.DataFrame(
            {"close": [close - 0.5, close]}
        )
    return repo


def test_intraday_uses_spot_price() -> None:
    """盘中: spot 快照命中则直接返回实时价, 不触 DAL."""
    repo = _mock_repo(close=9.9)
    with patch.object(lm, "is_market_open", return_value=True), \
         patch.object(lm, "_refresh_spot_map", return_value={"000001.SZ": 10.5}), \
         patch.object(lm, "get_repository", return_value=repo):
        assert lm.get_realtime_price("000001.SZ") == 10.5
    repo.get_daily_prices.assert_not_called()


def test_intraday_spot_miss_falls_back_to_dal() -> None:
    """盘中: spot 缺该票(新股/源故障) → 回落 DAL 最近收盘价."""
    repo = _mock_repo(close=9.9)
    with patch.object(lm, "is_market_open", return_value=True), \
         patch.object(lm, "_refresh_spot_map", return_value={"600000.SH": 8.0}), \
         patch.object(lm, "get_repository", return_value=repo):
        assert lm.get_realtime_price("000001.SZ") == 9.9


def test_eod_uses_dal_close() -> None:
    """盘后: 直接走 DAL 路径(不拉 spot 快照)."""
    repo = _mock_repo(close=12.3)
    with patch.object(lm, "is_market_open", return_value=False), \
         patch.object(lm, "_refresh_spot_map") as refresh, \
         patch.object(lm, "get_repository", return_value=repo):
        assert lm.get_realtime_price("000001.SZ") == 12.3
    refresh.assert_not_called()


def test_spot_suffix_mapping() -> None:
    assert lm._spot_suffix("600000") == ".SH"
    assert lm._spot_suffix("688141") == ".SH"
    assert lm._spot_suffix("000001") == ".SZ"
    assert lm._spot_suffix("300489") == ".SZ"
    assert lm._spot_suffix("830799") == ".BJ"


def _conn_with_latest(latest: str | None):
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = (latest,)
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    return ctx


def test_is_trading_day_recent_row_within_5d() -> None:
    """最近行情行距今 ≤5 天(周末/周一场景) → 视为交易日.

    库中最新行 20260821(周五), 目标日 20260824(下周一) 距 3 天."""
    with patch.object(lm, "get_market_conn", return_value=_conn_with_latest("20260821")):
        assert lm.is_trading_day("20260824") is True


def test_is_trading_day_stale_row_over_5d() -> None:
    """最近行情行距今 >5 天(长假中段) → 非交易日.

    库中最新行 20260930, 目标日 20261008(国庆假期中) 距 8 天."""
    with patch.object(lm, "get_market_conn", return_value=_conn_with_latest("20260930")):
        assert lm.is_trading_day("20261008") is False


def test_is_trading_day_empty_db() -> None:
    with patch.object(lm, "get_market_conn", return_value=_conn_with_latest(None)):
        assert lm.is_trading_day() is False
