"""长图生成+出口数字闸测试(内存库,不渲染真图)."""

from __future__ import annotations

import sqlite3
import time

import pandas as pd
import pytest

from davis_analyzer.surge import db, longpic


@pytest.fixture()
def mem_conn():
    conn = sqlite3.connect(":memory:")
    db.ensure_tables(conn)
    conn.execute(
        "INSERT INTO surge_snapshot (trade_date, ts_code, name, industry, pct_chg,"
        " pos_250d, dist_ma60, elg_net_d0, lg_net_5d, winner_rate,"
        " winner_delta_5d, resistance_dist, support_dist, resistance_price,"
        " support_price, weight_avg, cost_5pct, composite, hype_count,"
        " risk_flag_count, rank, fetched_at) VALUES"
        " ('20260918','000001.SZ','测试股','银行',8.4,0.30,0.05,"
        " 11890.0, 20000.0, 99.4, 3.2, 0.209, -0.146, 48.99, 39.37,"
        " 39.4, 38.2, 58.3, 3, 0, 1, ?)", (time.time(),))
    conn.execute(
        "INSERT INTO surge_tags VALUES ('20260918','000001.SZ','平台突破',?)", (time.time(),))
    conn.execute(
        "INSERT INTO major_events VALUES ('000002.SZ','20260801','ma','重组报告书',"
        "'positive','cninfo',?)", (time.time(),))
    conn.execute(
        "INSERT INTO cyq_perf_cache VALUES ('000001.SZ','20260918',1,2,3,4,5,6,7,8,9,?)",
        (time.time(),))
    conn.commit()
    yield conn
    conn.close()


def _snap_df() -> pd.DataFrame:
    return pd.DataFrame([{
        "trade_date": "20260918", "ts_code": "000001.SZ", "name": "测试股",
        "industry": "银行", "pct_chg": 8.4, "pos_250d": 0.30, "dist_ma60": 0.05,
        "elg_net_d0": 11890.0, "lg_net_5d": 20000.0, "winner_rate": 99.4,
        "winner_delta_5d": 3.2, "resistance_dist": 0.209, "support_dist": -0.146,
        "resistance_price": 48.99, "support_price": 39.37, "weight_avg": 39.4,
        "cost_5pct": 38.2, "composite": 58.3, "hype_count": 3,
        "risk_flag_count": 0, "rank": 1, "hype_tags": '["增持"]',
        "risk_flags": "[]"}])


def test_gate_passes_on_same_source_numbers(mem_conn):
    stats = longpic.collect_stats(mem_conn, "20260918")
    facts = longpic.snapshot_facts(mem_conn, "20260918", stats)
    html = longpic.build_full_html(_snap_df(), {"000001.SZ": ["平台突破"]}, stats)
    bad = longpic.gate_longpic(html, facts)
    assert bad == [], f"同源生成不应有未锚定数字: {bad[:10]}"


def test_gate_catches_tampered_number(mem_conn):
    stats = longpic.collect_stats(mem_conn, "20260918")
    facts = longpic.snapshot_facts(mem_conn, "20260918", stats)
    html = longpic.build_full_html(_snap_df(), {"000001.SZ": ["平台突破"]}, stats)
    tampered = html.replace("58.3", "88.8")  # 模板层手拼错数字
    bad = longpic.gate_longpic(tampered, facts)
    assert any("88.8" in t for t in bad)


def test_pattern_html_gate_passes_with_pattern_anchors(mem_conn):
    """形态参数须入锚(boom_pct/vol_decay/plateau/breakout),副本长图零未锚定."""
    import time as _t
    mem_conn.execute(
        "INSERT INTO surge_pattern_hits (trade_date, ts_code, boom_date, boom_pct,"
        " boom_vol_ratio, pullback_start, pullback_end, pullback_depth, vol_decay,"
        " plateau_high, plateau_days, breakout_pct, fetched_at) VALUES"
        " ('20260918','000001.SZ','20260901',7.2,2.9,'20260902','20260917',"
        " 0.05,0.65,10.5,20,0.076,?)", (_t.time(),))
    mem_conn.commit()
    stats = longpic.collect_stats(mem_conn, "20260918")
    facts = longpic.snapshot_facts(mem_conn, "20260918", stats)
    pat = pd.DataFrame([{
        "trade_date": "20260918", "ts_code": "000001.SZ", "boom_date": "20260901",
        "boom_pct": 7.2, "boom_vol_ratio": 2.9, "pullback_start": "20260902",
        "pullback_end": "20260917", "pullback_depth": 0.05, "vol_decay": 0.65,
        "plateau_high": 10.5, "plateau_days": 20, "breakout_pct": 0.076}])
    html = longpic.build_pattern_html(pat, _snap_df(), stats)
    bad = longpic.gate_longpic(html, facts)
    assert bad == [], f"副本长图未锚定: {bad[:10]}"


def test_stats_counts(mem_conn):
    stats = longpic.collect_stats(mem_conn, "20260918")
    assert stats.pool_n == 1
    assert stats.event_counts.get("ma") == 1
    assert stats.tag_top == [("平台突破", 1)]
    assert stats.pattern_n == 0
    assert stats.top1 == ("000001.SZ", "测试股", 58.3)
