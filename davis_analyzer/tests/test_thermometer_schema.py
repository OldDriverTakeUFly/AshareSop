"""thermometer 新表 schema 与常量存在性。"""
from __future__ import annotations

THERMO_TABLES = ["sw_index", "sw_daily", "sw_member", "sector_moneyflow_daily",
                 "ths_index", "ths_daily", "ths_member",
                 "thermometer_sector", "thermometer_market"]


def test_thermo_tables_created(tmp_path):
    from stockhot.data_layer import market_db
    db = tmp_path / "t.db"
    market_db.init_db(db)
    conn = market_db.get_connection(db)
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for t in THERMO_TABLES:
            assert t in names, f"missing table {t}"
        # 二次 init 幂等
        market_db.init_db(db)
    finally:
        conn.close()


def test_constants_and_config():
    from davis_analyzer.core import config, constants
    assert abs(sum(constants.THERMOMETER_WEIGHTS.values()) - 1.0) < 1e-9
    assert abs(sum(constants.THERMOMETER_MARKET_DIM_WEIGHTS.values()) - 1.0) < 1e-9
    assert set(constants.THERMOMETER_WEIGHTS) == {"momentum", "flow", "volume", "trend", "limit"}
    assert config.THERMOMETER_REPORTS_DIR.exists()
