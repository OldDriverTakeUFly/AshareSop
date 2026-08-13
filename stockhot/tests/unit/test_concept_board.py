"""concept_board 概念板块拉取的重试逻辑测试.

背景：新浪/东财行情接口存在瞬时 RemoteDisconnected（连接被服务端重置），
原实现每源只试 1 次、无重试，一次网络抖动即导致盘后报告缺失概念板块维度。
要求：瞬时异常时按指数退避重试，重试后成功可恢复；多次重试仍失败返回空。
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pandas as pd

import stockhot.concept_board as cb


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "板块": [f"概念{i}" for i in range(6)],
            "涨跌幅": [5.0, 4.0, 3.0, -1.0, -2.0, -3.0],
        }
    )


def test_fetch_concept_top5_retries_transient_error(monkeypatch):
    """新浪源第一次抛连接异常、第二次成功 → 重试后正常返回 top5."""
    calls = {"sina": 0}

    def fake_ak_stock_sector_spot(indicator="概念"):
        calls["sina"] += 1
        if calls["sina"] == 1:
            raise ConnectionError("Connection aborted., RemoteDisconnected")
        return _sample_df()

    fake_ak = SimpleNamespace(stock_sector_spot=fake_ak_stock_sector_spot)
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)
    monkeypatch.setattr(cb.time, "sleep", lambda s: None)

    top5_up, top5_dn = cb.fetch_concept_top5()

    assert calls["sina"] == 2  # 重试了一次
    assert len(top5_up) == 5
    assert top5_up[0].name == "概念0"
    assert top5_dn[0].name == "概念5"


def test_fetch_concept_top5_all_retries_exhausted(monkeypatch):
    """新浪/东财持续失败 → 每源各试满上限后返回空列表，不抛异常."""
    calls = {"sina": 0, "em": 0}

    def fake_sina(indicator="概念"):
        calls["sina"] += 1
        raise ConnectionError("Remote end closed connection")

    def fake_em():
        calls["em"] += 1
        raise ConnectionError("Remote end closed connection")

    fake_ak = SimpleNamespace(
        stock_sector_spot=fake_sina, stock_board_concept_name_em=fake_em
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)
    monkeypatch.setattr(cb.time, "sleep", lambda s: None)

    top5_up, top5_dn = cb.fetch_concept_top5()

    assert top5_up == [] and top5_dn == []
    assert calls["sina"] == 3  # 首次 + 2 次重试
    assert calls["em"] == 3
