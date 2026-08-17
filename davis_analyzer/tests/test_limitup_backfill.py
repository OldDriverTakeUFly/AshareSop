"""backfill.py 断点续传/幂等/字段映射测试 + backfill CLI 参数校验测试。"""

from __future__ import annotations

import sqlite3
import sys

import pandas as pd
import pytest

from davis_analyzer.limitup import backfill, cli


def _raw_df() -> pd.DataFrame:
    return pd.DataFrame(
        [{
            "trade_date": "20240102", "ts_code": "603311.SH", "industry": "家电",
            "name": "金海高科", "pct_chg": 10.0, "close": 10.01, "amount": 5e8,
            "limit_times": 2, "float_market_value": 8e9, "total_market_value": 1e10,
            "turnover_rate": 12.5, "fd_amount": 5e7, "first_time": "093000",
            "last_time": "145500", "open_times": 0, "limit": "U",
        }]
    )


def _seed_cal(conn: sqlite3.Connection, *dates: str) -> None:
    conn.executemany(
        "INSERT INTO daily_price VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [("000001.SH", d, 1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 1.0, None)
         for d in dates],
    )
    conn.commit()


def test_backfill_writes_and_maps(limitup_db: sqlite3.Connection) -> None:
    _seed_cal(limitup_db, "20240102")
    backfill.ensure_ext_table(limitup_db)
    calls: list[tuple[str, str]] = []

    def fetch(trade_date: str, limit_type: str) -> pd.DataFrame:
        calls.append((trade_date, limit_type))
        df = _raw_df()
        df["trade_date"] = trade_date
        return df if limit_type == "U" else pd.DataFrame()

    result = backfill.backfill(limitup_db, "20240102", "20240102", fetch)
    assert result["days_done"] == 1 and result["rows_written"] == 1
    assert len(calls) == 3  # U/Z/D 三次
    row = limitup_db.execute(
        "SELECT ts_code, pool_kind, seal_amount, consecutive_boards, sector "
        "FROM limit_pool"
    ).fetchone()
    assert row[0] == "603311" and row[1] == "limit_up"
    assert row[2] == 5e7 and row[3] == 2 and row[4] == "家电"
    ext = limitup_db.execute("SELECT float_mv FROM limit_pool_ext").fetchone()
    assert ext[0] == 8e9


def test_backfill_idempotent_skips_done_day(limitup_db: sqlite3.Connection) -> None:
    _seed_cal(limitup_db, "20240102")
    backfill.ensure_ext_table(limitup_db)
    backfill.backfill(limitup_db, "20240102", "20240102", lambda d, t: _raw_df())
    result = backfill.backfill(limitup_db, "20240102", "20240102", lambda d, t: _raw_df())
    assert result["days_skipped"] == 1 and result["rows_written"] == 0


def test_backfill_handles_none_fetch(limitup_db: sqlite3.Connection) -> None:
    _seed_cal(limitup_db, "20240102")
    backfill.ensure_ext_table(limitup_db)
    result = backfill.backfill(limitup_db, "20240102", "20240102",
                               lambda d, t: None)
    assert result["days_done"] == 0 and result["rows_written"] == 0


def test_probe_earliest_binary_search(limitup_db: sqlite3.Connection) -> None:
    _seed_cal(limitup_db, "20230103", "20230104", "20230105", "20230106",
              "20240102", "20240103")

    def fetch(trade_date: str, limit_type: str) -> pd.DataFrame | None:
        return _raw_df() if trade_date >= "20230105" and limit_type == "U" else None

    assert backfill.probe_earliest(limitup_db, fetch) == "20230105"


def test_cli_probe_mode_parses_without_start() -> None:
    args = cli._build_parser().parse_args(["backfill", "--probe"])
    assert args.command == "backfill"
    assert args.probe is True and args.start is None


def test_cli_backfill_without_start_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["davis_analyzer.limitup", "backfill"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "--start 仅在 probe 模式可省略" in capsys.readouterr().err
