"""日报渲染:章节齐全、表格式 markdown 可读."""
from __future__ import annotations


def _conn(tmp_path):
    from stockhot.data_layer import market_db
    db = tmp_path / "t.db"
    market_db.init_db(db)
    return market_db.get_connection(db)


def _seed(conn) -> str:
    day = "20260911"
    conn.execute(
        "INSERT INTO thermometer_market (trade_date,temperature,regime_label,"
        "trend_dim,width_dim,volume_dim,flow_dim,sentiment_dim,fetched_at) "
        "VALUES (?,42.0,'温和',0.4,0.5,0.45,0.38,0.6,0)", (day,))
    for i in range(12):
        for level in ("L1", "L2"):
            conn.execute(
                "INSERT INTO thermometer_sector (trade_date,level,index_code,name,"
                "temperature,delta_temp5,hot_streak,mom_score,flow_score,vol_score,"
                "trend_score,limit_score,composite_z,fetched_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
                (day, level, f"8010{i:02d}.SI", f"行业{i}", 90 - i * 5, 3.0 - i,
                 5 if i == 0 else 0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0))
    conn.commit()
    return day


def test_write_daily_report(tmp_path, monkeypatch):
    from davis_analyzer.thermometer import report

    conn = _conn(tmp_path)
    try:
        day = _seed(conn)
        monkeypatch.setattr(report, "REPORTS_DIR", tmp_path)
        path = report.write_daily_report(conn, day)
        text = path.read_text(encoding="utf-8")
        assert "板块温度计" in path.name
        for sec in ("L1 温度榜", "L2 温度榜", "升温榜", "降温榜", "高温预警", "温度轮动", "大盘温度", "数据完整性"):
            assert sec in text, sec
        assert "行业0" in text and "42.0" in text
        assert "温和" in text
    finally:
        conn.close()


def test_report_empty_market(tmp_path, monkeypatch):
    """缺大盘温度行时日报仍可生成(该节缺省说明)."""
    from davis_analyzer.thermometer import report

    conn = _conn(tmp_path)
    try:
        day = _seed(conn)
        conn.execute("DELETE FROM thermometer_market")
        conn.commit()
        monkeypatch.setattr(report, "REPORTS_DIR", tmp_path)
        path = report.write_daily_report(conn, day)
        assert path.exists()
    finally:
        conn.close()
