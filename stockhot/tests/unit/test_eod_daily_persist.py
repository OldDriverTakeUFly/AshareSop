"""eod_review 盘后日线写穿落库测试（2026-08 daily_price 断供事故修复）.

背景：盘后总结此前直拉全市场行情做总结但不落库（历史 NaN 事故故意绕开
DAL），daily_price 依赖周末批量——2026-08-10 批量空窗后断供。修复：
快照仍直拉（OHLCV 完整性不受缓存污染），拉到后 best-effort 写穿落库。
"""

from __future__ import annotations

import pandas as pd

from stockhot.eod_review.data_layer import _persist_daily


class _FakeRepo:
    def __init__(self, exc: Exception | None = None) -> None:
        self.calls: list[tuple[pd.DataFrame, pd.DataFrame]] = []
        self.exc = exc

    def persist_daily_snapshot(self, daily_df, adj_df):
        if self.exc:
            raise self.exc
        self.calls.append((daily_df, adj_df))
        return len(daily_df)


class _FakeGW:
    def __init__(
        self,
        adj: pd.DataFrame | None = None,
        exc: Exception | None = None,
    ) -> None:
        self._adj = adj
        self.exc = exc

    def get_adj_factor(self, date: str) -> pd.DataFrame:
        if self.exc:
            raise self.exc
        return self._adj if self._adj is not None else pd.DataFrame()


def _df() -> pd.DataFrame:
    return pd.DataFrame(
        {"ts_code": ["000001.SZ"], "trade_date": ["20260817"], "close": [10.5]}
    )


def test_persist_daily_success_passes_adj() -> None:
    """正常路径：价格与复权因子一起交给 repository."""
    repo = _FakeRepo()
    gw = _FakeGW(adj=pd.DataFrame({"trade_date": ["20260817"], "adj_factor": [1.2]}))
    _persist_daily(repo, gw, "20260817", _df())
    assert len(repo.calls) == 1
    daily, adj = repo.calls[0]
    assert len(daily) == 1
    assert adj["adj_factor"].tolist() == [1.2]


def test_persist_daily_adj_failure_still_persists_prices() -> None:
    """adj_factor 拉取失败不阻断：价格照常落库（因子留空可后补）."""
    repo = _FakeRepo()
    gw = _FakeGW(exc=RuntimeError("adj down"))
    _persist_daily(repo, gw, "20260817", _df())
    assert len(repo.calls) == 1
    assert repo.calls[0][1].empty


def test_persist_daily_repo_failure_does_not_raise() -> None:
    """落库失败只告警，绝不阻断盘后总结主流程."""
    repo = _FakeRepo(exc=RuntimeError("db down"))
    gw = _FakeGW()
    _persist_daily(repo, gw, "20260817", _df())  # 不抛异常即通过
