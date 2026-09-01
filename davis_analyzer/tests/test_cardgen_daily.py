"""每日复盘卡生成器:迷你 stockhot.db fixture → build → run_validation 四道闸全过。"""
import json
import sqlite3
from pathlib import Path

import pytest

from davis_analyzer.cardgen import daily

DAY = "2026-09-01"
PREV = "2026-08-31"


def _mk_db(tmp_path: Path, *, with_lhb: bool = True, with_prev: bool = True,
           with_inst: bool = True) -> Path:
    db = tmp_path / "stockhot.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE daily_data(
        id INTEGER PRIMARY KEY AUTOINCREMENT, trade_date TEXT NOT NULL,
        data_type TEXT NOT NULL, data_json TEXT NOT NULL,
        UNIQUE(trade_date, data_type))""")
    con.execute("""CREATE TABLE analysis_results(
        id INTEGER PRIMARY KEY AUTOINCREMENT, trade_date TEXT NOT NULL,
        analysis_type TEXT NOT NULL, result_json TEXT NOT NULL,
        UNIQUE(trade_date, analysis_type))""")
    pool = [
        {"code": "002084.SZ", "name": "海鸥住工", "change_pct": 10.03,
         "seal_amount": 171719954.0, "max_board": 7.0, "consecutive_boards": 7.0,
         "sector": "家居用品", "broken_count": 0.0, "first_seal_time": "92500",
         "last_seal_time": "92500", "turnover_rate": 13.81},
        {"code": "002855.SZ", "name": "捷荣技术", "change_pct": 10.0,
         "seal_amount": 80000000.0, "max_board": 6.0, "consecutive_boards": 6.0,
         "sector": "消费电子", "broken_count": 1.0, "first_seal_time": "100001",
         "last_seal_time": "100001", "turnover_rate": 8.5},
        {"code": "600371.SH", "name": "万向德农", "change_pct": 10.0,
         "seal_amount": 60000000.0, "max_board": 6.0, "consecutive_boards": 6.0,
         "sector": "种植业", "broken_count": 0.0, "first_seal_time": "093000",
         "last_seal_time": "093000", "turnover_rate": 5.2},
        {"code": "000560.SZ", "name": "我爱我家", "change_pct": 10.0,
         "seal_amount": 40000000.0, "max_board": 3.0, "consecutive_boards": 3.0,
         "sector": "房地产", "broken_count": 2.0, "first_seal_time": "133003",
         "last_seal_time": "140500", "turnover_rate": 21.7},
    ]
    con.execute("INSERT INTO daily_data(trade_date, data_type, data_json) VALUES(?,?,?)",
                (DAY, "limit_up_pool", json.dumps(pool, ensure_ascii=False)))
    con.execute("INSERT INTO daily_data(trade_date, data_type, data_json) VALUES(?,?,?)",
                (DAY, "broken_pool", json.dumps([{"code": "300999.SZ", "name": "X股"}])))
    analysis = {"consecutive_boards": [
        {"board_count": 7, "stocks": [{"code": "002084.SZ", "name": "海鸥住工"}]},
        {"board_count": 6, "stocks": [{"code": "002855.SZ", "name": "捷荣技术"},
                                      {"code": "600371.SH", "name": "万向德农"}]},
        {"board_count": 3, "stocks": [{"code": "000560.SZ", "name": "我爱我家"}]}]}
    con.execute("INSERT INTO analysis_results(trade_date, analysis_type, result_json) VALUES(?,?,?)",
                (DAY, "limit_up_analysis", json.dumps(analysis, ensure_ascii=False)))
    if with_prev:
        prev = {"consecutive_boards": [
            {"board_count": 6, "stocks": [{"code": "002084.SZ", "name": "海鸥住工"}]}]}
        con.execute("INSERT INTO analysis_results(trade_date, analysis_type, result_json) VALUES(?,?,?)",
                    (PREV, "limit_up_analysis", json.dumps(prev, ensure_ascii=False)))
    if with_lhb:
        detail = [
            {"code": "000892.SZ", "name": "欢瑞世纪", "reason": "连续三个交易日内,涨幅偏离值累计达到20%的证券",
             "close_price": 4.73, "change_pct": 10.0, "net_buy_amount": 37288799.9,
             "buy_amount": 68847694.0, "sell_amount": 31558894.1, "list_date": "20260901"},
            {"code": "000560.SZ", "name": "我爱我家", "reason": "日换手率达到20%的前5只证券",
             "close_price": 3.19, "change_pct": 10.0, "net_buy_amount": -137245894.65,
             "buy_amount": 300034118.38, "sell_amount": 437280013.03, "list_date": "20260901"},
            {"code": "000011.SZ", "name": "深物业A", "reason": "日跌幅偏离值达到7%的前5只证券",
             "close_price": 9.16, "change_pct": -9.0367, "net_buy_amount": -46085510.86,
             "buy_amount": 53578781.34, "sell_amount": 99664292.2, "list_date": "20260901"},
        ]
        con.execute("INSERT INTO daily_data(trade_date, data_type, data_json) VALUES(?,?,?)",
                    (DAY, "dragon_tiger_detail", json.dumps(detail, ensure_ascii=False)))
        dt_analysis = {
            "brokers": [
                {"broker_name": "国泰海通证券股份有限公司上海自贸试验区第二分公司",
                 "buy_amount": 299679625.98, "sell_amount": 0.0, "net_amount": 299679625.98},
                {"broker_name": "开源证券股份有限公司西安西大街证券营业部",
                 "buy_amount": 491999114.42, "sell_amount": 226286311.73, "net_amount": 265712802.69},
            ],
            "institutional": ([{"inst_code": "000892.SZ", "inst_name": "机构专用",
                                "buy_amount": 50000000.0, "sell_amount": 20000000.0,
                                "net_amount": 30000000.0}] if with_inst else []),
        }
        con.execute("INSERT INTO analysis_results(trade_date, analysis_type, result_json) VALUES(?,?,?)",
                    (DAY, "dragon_tiger", json.dumps(dt_analysis, ensure_ascii=False)))
    con.commit()
    con.close()
    return db


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "卡片"
    root.mkdir()
    monkeypatch.setenv("CARDGEN_PROJECT_ROOT", str(root))
    monkeypatch.setenv("CARDGEN_LEDGER_DB", str(tmp_path / "cards.db"))
    db = _mk_db(tmp_path)
    return root, db


class TestLadder:
    def test_generated_project_passes_all_gates(self, env):
        root, db = env
        proj, topic, report = daily.generate("ladder", DAY, root, None, db)
        assert topic == f"连板天梯/{DAY}"
        assert (proj / "facts.json").exists() and (proj / "cards.spec.json").exists()
        assert report.passed, [f"{f.gate}:{f.detail}" for f in report.failures]

    def test_promotion_wording_when_higher(self, env):
        _, db = env
        facts, spec = daily.build_ladder(DAY, daily.fetch_day_bundle(db, DAY))
        texts = json.dumps(spec, ensure_ascii=False)
        assert "晋级" in texts  # 今日7板 vs 昨日6板

    def test_flat_wording_when_equal(self, tmp_path):
        db = _mk_db(tmp_path)  # 默认昨日6板,先构造持平:把 prev 改成 7
        con = sqlite3.connect(db)
        prev = {"consecutive_boards": [
            {"board_count": 7, "stocks": [{"code": "002084.SZ", "name": "海鸥住工"}]}]}
        con.execute("UPDATE analysis_results SET result_json=? WHERE trade_date=?",
                    (json.dumps(prev, ensure_ascii=False), PREV))
        con.commit(); con.close()
        _, spec = daily.build_ladder(DAY, daily.fetch_day_bundle(db, DAY))
        assert "持平" in json.dumps(spec, ensure_ascii=False)

    def test_pullback_wording_when_lower(self, tmp_path):
        db = _mk_db(tmp_path)  # 构造回落:昨日8板
        con = sqlite3.connect(db)
        prev = {"consecutive_boards": [
            {"board_count": 8, "stocks": [{"code": "002084.SZ", "name": "海鸥住工"}]}]}
        con.execute("UPDATE analysis_results SET result_json=? WHERE trade_date=?",
                    (json.dumps(prev, ensure_ascii=False), PREV))
        con.commit(); con.close()
        _, spec = daily.build_ladder(DAY, daily.fetch_day_bundle(db, DAY))
        assert "回落" in json.dumps(spec, ensure_ascii=False)

    def test_no_prev_day_wording(self, tmp_path):
        db = _mk_db(tmp_path, with_prev=False)
        _, spec = daily.build_ladder(DAY, daily.fetch_day_bundle(db, DAY))
        assert "昨日无梯队数据" in json.dumps(spec, ensure_ascii=False)

    def test_missing_analysis_raises(self, tmp_path):
        db = _mk_db(tmp_path)
        con = sqlite3.connect(db)
        con.execute("DELETE FROM analysis_results WHERE analysis_type='limit_up_analysis'")
        con.commit(); con.close()
        with pytest.raises(daily.DailyDataMissing):
            daily.fetch_day_bundle(db, DAY)


class TestLhb:
    def test_generated_project_passes_all_gates(self, env):
        root, db = env
        proj, topic, report = daily.generate("lhb", DAY, root, None, db)
        assert topic == f"龙虎榜/{DAY}"
        assert report.passed, [f"{f.gate}:{f.detail}" for f in report.failures]
        texts = json.dumps(json.loads((proj / "cards.spec.json").read_text(encoding="utf-8")),
                           ensure_ascii=False)
        assert "买入额" not in texts and "卖出额" not in texts   # 金额口径红线

    def test_missing_dragon_tiger_raises(self, tmp_path):
        db = _mk_db(tmp_path, with_lhb=False)
        root = tmp_path / "卡片"; root.mkdir()
        bundle = daily.fetch_day_bundle(db, DAY)  # fetch 不拦 lhb 维度(天梯数据齐即可)
        assert bundle["lhb_detail"] == []
        with pytest.raises(daily.DailyDataMissing):
            daily.generate("lhb", DAY, root, None, db)  # generate('lhb') 在 detail 为空时拒绝

    def test_empty_institutional_degrades(self, tmp_path, monkeypatch):
        root = tmp_path / "卡片"; root.mkdir()
        monkeypatch.setenv("CARDGEN_PROJECT_ROOT", str(root))
        monkeypatch.setenv("CARDGEN_LEDGER_DB", str(tmp_path / "cards.db"))
        db = _mk_db(tmp_path, with_inst=False)
        _, _, report = daily.generate("lhb", DAY, root, None, db)
        assert report.passed

    def test_reason_labelled_digit_free(self, env):
        _, db = env
        facts, spec = daily.build_lhb(DAY, daily.fetch_day_bundle(db, DAY))
        texts = json.dumps(spec, ensure_ascii=False)
        assert "换手达标" in texts and "三日涨幅偏离" in texts
        assert "20%" not in texts  # 原因文本里的数字必须被映射掉

    def test_institutional_filters_non_pure_rows(self, tmp_path):
        # Task 3 交接:只统计 inst_name='机构专用',沪股通/营业部不计入
        db = _mk_db(tmp_path)
        con = sqlite3.connect(db)
        row = con.execute("SELECT result_json FROM analysis_results WHERE analysis_type='dragon_tiger'").fetchone()
        dt = json.loads(row[0])
        dt["institutional"] = [
            {"inst_code": "000892.SZ", "inst_name": "机构专用", "net_amount": 30000000.0},
            {"inst_code": "600519.SH", "inst_name": "沪股通专用", "net_amount": -90000000.0},
        ]
        con.execute("UPDATE analysis_results SET result_json=? WHERE analysis_type='dragon_tiger'",
                    (json.dumps(dt, ensure_ascii=False),))
        con.commit(); con.close()
        facts, spec = daily.build_lhb(DAY, daily.fetch_day_bundle(db, DAY))
        facts_by_id = {f.id: f for f in facts}
        assert facts_by_id["ist1_yi"].display == "+0.3亿"  # 只含机构专用净额(0.30 亿,去尾零)
