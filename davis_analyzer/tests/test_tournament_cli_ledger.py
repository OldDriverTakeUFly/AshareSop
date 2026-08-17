"""run/replay/champions CLI 契约测试（mock 端到端，无 DB/网络）。

沿用 test_tournament_evolution_campaign._patch_evolve_env 的 mock 模式：
ledger.open_db → 内存库、TushareClient → MagicMock、trading_calendar → 合成
日历、evaluate_window → 合成 WindowReport（stats 真实构造）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

import davis_analyzer.market_regime as market_regime_mod
import davis_analyzer.tournament.judge as judge_mod
import davis_analyzer.tournament.ledger as ledger_mod
import davis_analyzer.tournament.report as report_mod
import davis_analyzer.tushare_client as tushare_client_mod
from davis_analyzer.backtest_report import PerformanceStats
from davis_analyzer.tournament.champions import incumbents
from davis_analyzer.tournament.cli import main
from davis_analyzer.tournament.judge import WindowReport
from davis_analyzer.tournament.ledger import LedgerRecord, append_record

_RUN_ARGS = ["run", "--start", "20230102", "--end", "20250821"]
_REPLAY_ARGS = ["replay", "--start", "20230102", "--end", "20250821"]


def _stats(sharpe: float) -> PerformanceStats:
    return PerformanceStats(
        total_return_pct=sharpe * 10, annualized_return_pct=sharpe * 10,
        sharpe_ratio=sharpe, max_drawdown_pct=-8.0, win_rate_pct=60.0,
        turnover_per_rebalance=1.0, num_trades=20, num_rebalances=12,
        avg_holding_count=10.0, total_cost=100.0,
    )


def _fake_evaluate_window(self, start, end, params_by_participant=None):
    """两项参赛者的合成窗口报告（stats 真实构造，regime 固定 risk_on）。"""
    return {
        "davis_balanced": WindowReport(
            "davis_balanced", start, end, stats=_stats(1.5),
            regime="risk_on", na_reason=None),
        "benchmark_sse": WindowReport(
            "benchmark_sse", start, end, stats=_stats(0.5),
            regime="risk_on", na_reason=None),
    }


@pytest.fixture
def cli_env(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """run/replay 分支的全部外部触点（600 日合成日历）。

    台账用 tmp 文件库而非 :memory:：cli 分支 append 后会 conn.close()，
    文件库下测试自己的连接不受影响。
    """
    db_path = tmp_path / "tournament_ledger.db"
    conn = sqlite3.connect(db_path)
    ledger_mod.ensure_tables(conn)
    calendar = [date(2023, 1, 2) + timedelta(days=i) for i in range(600)]
    monkeypatch.setattr(ledger_mod, "open_db", lambda: _open_file_db(db_path))
    monkeypatch.setattr(tushare_client_mod, "TushareClient", MagicMock)
    monkeypatch.setattr(judge_mod, "trading_calendar", lambda client, start, end: calendar)
    monkeypatch.setattr(judge_mod.JudgeHarness, "evaluate_window", _fake_evaluate_window)
    monkeypatch.setattr(
        market_regime_mod, "get_market_regime_with_confirm",
        lambda trade_date, confirm_days=3: "risk_on",
    )
    yield conn, calendar
    conn.close()


def _open_file_db(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    ledger_mod.ensure_tables(conn)
    return conn


def _ledger_rows(conn: sqlite3.Connection, op_type: str) -> list[tuple]:
    return conn.execute(
        "SELECT run_date, participants, params_version, oos_windows_used, detail "
        "FROM tournament_ledger WHERE op_type=? ORDER BY id",
        (op_type,),
    ).fetchall()


# ── run ──


def test_run_cli_writes_report_and_ledger(cli_env, monkeypatch, tmp_path, capsys) -> None:
    conn, _ = cli_env
    monkeypatch.setattr(report_mod, "TOURNAMENT_REPORTS_DIR", tmp_path)
    rc = main(list(_RUN_ARGS))
    assert rc == 0
    report_path = tmp_path / "2025-08-21_tournament.md"
    assert report_path.exists()
    assert "策略锦标赛报告" in report_path.read_text(encoding="utf-8")
    assert "连续微调" not in report_path.read_text(encoding="utf-8")  # 干净台账无告警

    rows = _ledger_rows(conn, "run")
    assert len(rows) == 1
    run_date, participants_json, params_version, oos_used, detail_json = rows[0]
    assert run_date == "2025-08-21"
    participants = json.loads(participants_json)
    assert participants and all(len(p) == 2 for p in participants)  # (name, version)
    assert params_version == "TOURNAMENT-v1"
    assert oos_used > 0
    assert json.loads(detail_json)["report"].endswith("2025-08-21_tournament.md")
    assert "锦标赛报告已写入" in capsys.readouterr().out


def test_run_cli_report_warns_on_continual_tweaking(cli_env, monkeypatch, tmp_path) -> None:
    conn, _ = cli_env
    # 预插 3 条同 params_version 的 evolve 记录（30 天滚动窗口内）→ 触发微调检测
    d0 = date.today()
    for i in range(3):
        append_record(conn, LedgerRecord(
            op_type="evolve", run_date=d0 - timedelta(days=i),
            participants=[("davis_balanced", "v1")],
            params_version="campaign-same", oos_windows_used=5, detail={},
        ))
    monkeypatch.setattr(report_mod, "TOURNAMENT_REPORTS_DIR", tmp_path)
    rc = main(list(_RUN_ARGS))
    assert rc == 0
    text = (tmp_path / "2025-08-21_tournament.md").read_text(encoding="utf-8")
    assert text.startswith("⚠️")
    assert "连续微调" in text  # 检出告警必须上报告
    assert _ledger_rows(conn, "run")  # 告警不阻断，run 台账照常追加


# ── replay ──


def test_replay_cli_writes_csvs_and_ledger(
    cli_env, monkeypatch, tmp_path, capsys,
) -> None:
    import davis_analyzer.config as config_mod

    conn, calendar = cli_env
    monkeypatch.setattr(config_mod, "TOURNAMENT_REPORTS_DIR", tmp_path)
    rc = main(list(_REPLAY_ARGS))
    assert rc == 0
    last_day = calendar[-1]
    stamp = last_day.isoformat()
    meta_csv = tmp_path / f"{stamp}_meta_series.csv"
    forward_csv = tmp_path / f"{stamp}_forward_curve.csv"
    assert meta_csv.exists() and forward_csv.exists()  # 两个 CSV 落盘

    rows = _ledger_rows(conn, "replay")
    assert len(rows) == 1
    run_date, participants_json, params_version, oos_used, detail_json = rows[0]
    assert run_date == stamp  # run_date = windows[-1][1]
    participants = json.loads(participants_json)
    assert participants  # 与 default_participants 同源
    assert params_version == "TOURNAMENT-v1"
    assert oos_used > 0
    detail = json.loads(detail_json)
    assert detail["meta_csv"].endswith(f"{stamp}_meta_series.csv")
    assert detail["forward_csv"].endswith(f"{stamp}_forward_curve.csv")
    out = capsys.readouterr().out
    assert "meta 序列" in out and "前向曲线" in out


# ── champions ──


def test_champions_promote_cli(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    conn = sqlite3.connect(":memory:")
    ledger_mod.ensure_tables(conn)
    monkeypatch.setattr(ledger_mod, "open_db", lambda: conn)
    append_record(conn, LedgerRecord(
        op_type="evolve", run_date=date.today(),
        participants=[("davis_balanced", "v1")], params_version="campaign-x",
        oos_windows_used=10,
        detail={"ok": True, "best_params": {"momentum_weight": 0.35},
                "improvements": [0.1], "decay": 0.1, "finals_pass": True, "reasons": []},
    ))
    try:
        rc = main(["champions", "promote"])
        assert rc == 0
        inc = incumbents(conn)
        assert inc and inc[0].participant == "davis_balanced"
        assert inc[0].params == {"momentum_weight": 0.35}
        assert _ledger_rows(conn, "promote")  # promote 动作本身也记台账
        assert "已晋升" in capsys.readouterr().out
    finally:
        conn.close()


def test_champions_verify_cli_empty_state(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    conn = sqlite3.connect(":memory:")
    ledger_mod.ensure_tables(conn)
    monkeypatch.setattr(ledger_mod, "open_db", lambda: conn)
    try:
        rc = main(["champions", "verify"])  # 空 presets + 无现任 → 一致
        assert rc == 0
        assert "一致" in capsys.readouterr().out
    finally:
        conn.close()
