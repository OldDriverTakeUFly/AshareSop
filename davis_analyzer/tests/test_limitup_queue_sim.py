"""queue_sim 排队模拟测试（纯函数合成分钟线 + in-memory DB + mock fetch）."""

from __future__ import annotations

import sqlite3

import pandas as pd

from davis_analyzer.limitup import queue_sim


def _mins(rows: list[tuple[str, float, float]]) -> pd.DataFrame:
    # rows: (time, high, low)
    return pd.DataFrame(
        [{"time": t, "high": h, "low": lo, "open": lo, "close": h, "vol": 100}
         for t, h, lo in rows]
    )


def test_simulate_never_boarded() -> None:
    m = _mins([("09:31", 10.5, 10.2), ("09:32", 10.6, 10.3)])
    r = queue_sim.simulate_queue(m, pre_close=10.0, ratio=0.10)  # limit=11.0
    assert r == {"boarded": False, "filled": False, "limit_price": 11.0}


def test_simulate_one_word_board_unfilled() -> None:
    # 一字全天：high=low=11.0
    m = _mins([("09:31", 11.0, 11.0)] * 5)
    r = queue_sim.simulate_queue(m, pre_close=10.0, ratio=0.10)
    assert r["boarded"] and not r["filled"]
    assert r["first_touch"] == "09:31" and r["fill_time"] is None


def test_simulate_break_fills() -> None:
    # 09:31 上板，09:33（挂单后）low 跌破 11.0 → 成交于该分钟
    m = _mins([("09:31", 11.0, 10.9), ("09:32", 11.0, 11.0),
               ("09:33", 11.0, 10.8), ("09:34", 10.9, 10.7)])
    r = queue_sim.simulate_queue(m, pre_close=10.0, ratio=0.10)
    assert r["boarded"] and r["filled"]
    assert r["fill_time"] == "09:33"


def test_simulate_seal_minute_break_does_not_fill() -> None:
    # 上板分钟(09:31)当分钟 low<limit：挂单在其后——09:31 不成交，09:33 跌破才成交
    m = _mins([("09:31", 11.0, 10.85), ("09:32", 11.0, 11.0), ("09:33", 10.9, 10.8)])
    r = queue_sim.simulate_queue(m, pre_close=10.0, ratio=0.10)
    assert r["filled"] and r["fill_time"] == "09:33"


def test_run_queue_sim_end_to_end(limitup_db: sqlite3.Connection, monkeypatch) -> None:
    # 日历：0102(候选日) → 0103(监控日) → 0104(次日开盘)
    for d in ("20240102", "20240103", "20240104"):
        limitup_db.execute(
            "INSERT INTO daily_price VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("600100.SH", d, 10.0, 11.0, 10.0, 11.0, 10.0, 10.0, 0, 0, 1.0, None),
        )
    limitup_db.commit()

    def fake_cands(conn, day):
        return pd.DataFrame([{
            "ts_code": "600100.SH", "name": "甲", "enhanced": False,
            "seal_ratio": 0.03,
        }])

    class FakeClient:
        from types import SimpleNamespace as _NS

        _pro = _NS(stk_mins=lambda **kw: None)

        def _call(self, *a, **k):
            return _mins([("09:31", 11.0, 11.0), ("09:32", 11.0, 10.9),
                          ("09:33", 10.8, 10.7)])

    monkeypatch.setattr(queue_sim, "_MINS_CALL_GAP", 0)  # 测试不 sleep
    monkeypatch.setattr(queue_sim, "_last_mins_call", time_monotonic_offset())
    df = queue_sim.run_queue_sim(
        limitup_db, "20240103", FakeClient(), candidates_fn=fake_cands)
    assert len(df) == 1
    row = df.iloc[0]
    assert bool(row["boarded"]) and bool(row["filled"])
    assert abs(row["limit_price"] - 11.0) < 1e-9
    # 次日(0104) open=10.0 → ret = 10/11-1
    assert abs(row["ret_open_1"] - (10.0 / 11.0 - 1)) < 1e-9
    # 汇总行
    s = queue_sim.queue_summary(limitup_db, "20240103")
    assert "候选1 上板1 成交1" in s


def time_monotonic_offset() -> float:
    import time

    return time.monotonic() - 9999.0
