"""CLI 编排:mock 下游模块,断言 run 五步调用链与参数."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def fake_conn() -> MagicMock:
    return MagicMock()


def test_run_orchestration_order(fake_conn, tmp_path):
    from davis_analyzer.thermometer import cli

    called: list[str] = []

    def _rec(name):
        def f(*a, **kw):
            called.append(name)
            return {"rows": 0}
        return f

    gw = MagicMock()
    with patch.object(cli, "_conn", return_value=fake_conn), \
         patch.object(cli, "_gw", return_value=gw), \
         patch("davis_analyzer.limitup.db.latest_trade_date", return_value="20260911"), \
         patch.object(cli.data, "update_sw_daily_incremental",
                      side_effect=_rec("sw_incr")), \
         patch.object(cli.moneyflow_agg, "aggregate_sector_moneyflow",
                      side_effect=_rec("mf_agg")), \
         patch.object(cli.scoring, "score_history", side_effect=_rec("score")), \
         patch.object(cli.market_temp, "compute_market_history",
                      side_effect=lambda *a, **kw: MagicMock(length=0)), \
         patch.object(cli.report, "write_daily_report",
                      return_value=tmp_path / "r.md") as m_report, \
         patch.object(cli, "_build_card") as m_card:
        cli.cmd_run(argparse_ns(no_card=False))
        assert m_report.called and m_card.called
    # 调用链顺序:增量行情 → 资金聚合 → 评分 → 大盘 → 日报 → 卡片
    assert called == ["sw_incr", "mf_agg", "score"]
    m_report.assert_called_once()
    m_card.assert_called_once_with("20260911")


def argparse_ns(**kw):
    import argparse
    return argparse.Namespace(**kw)


def test_run_no_card_flag(fake_conn, tmp_path):
    from davis_analyzer.thermometer import cli

    gw = MagicMock()
    with patch.object(cli, "_conn", return_value=fake_conn), \
         patch.object(cli, "_gw", return_value=gw), \
         patch("davis_analyzer.limitup.db.latest_trade_date", return_value="20260911"), \
         patch.object(cli.data, "update_sw_daily_incremental"), \
         patch.object(cli.moneyflow_agg, "aggregate_sector_moneyflow"), \
         patch.object(cli.scoring, "score_history"), \
         patch.object(cli.market_temp, "compute_market_history"), \
         patch.object(cli.report, "write_daily_report", return_value=tmp_path / "r.md"), \
         patch.object(cli, "_build_card") as m_card:
        cli.cmd_run(argparse_ns(no_card=True))
        m_card.assert_not_called()


def test_backfill_universe_only(fake_conn):
    from davis_analyzer.thermometer import cli, universe

    gw = MagicMock()
    with patch.object(cli, "_conn", return_value=fake_conn), \
         patch.object(cli, "_gw", return_value=gw), \
         patch.object(universe, "refresh_sw_index", return_value=165) as m1, \
         patch.object(universe, "refresh_sw_member", return_value=8000) as m2, \
         patch.object(universe, "refresh_ths_index", return_value=395) as m3, \
         patch.object(universe, "refresh_ths_member", return_value=9000) as m4, \
         patch.object(cli.data, "backfill_sw_daily") as mb:
        cli.cmd_backfill(argparse_ns(universe_only=True, start="20220104", end=None))
        assert m1.called and m2.called and m3.called and m4.called
        mb.assert_not_called()  # universe-only 不碰行情回补
