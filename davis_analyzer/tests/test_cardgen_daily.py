"""每日复盘卡生成器:迷你 stockhot.db fixture → build → run_validation 四道闸全过。"""
import json
import sqlite3
from pathlib import Path

import pytest

from davis_analyzer.cardgen import daily, ledger

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

    def test_dedup_aggregates_by_code(self, tmp_path):
        # Important-1:同一 code 多行(不同上榜原因)须聚合成一行——净额求和、计数去重
        db = _mk_db(tmp_path)
        con = sqlite3.connect(db)
        row = con.execute(
            "SELECT data_json FROM daily_data WHERE data_type='dragon_tiger_detail'").fetchone()
        detail = json.loads(row[0])
        detail.append({"code": "000892.SZ", "name": "欢瑞世纪",
                       "reason": "日振幅达到15%的前5只证券", "close_price": 4.73,
                       "change_pct": 10.0, "net_buy_amount": 10000000.0,
                       "buy_amount": 20000000.0, "sell_amount": 10000000.0,
                       "list_date": "20260901"})
        con.execute("UPDATE daily_data SET data_json=? WHERE data_type='dragon_tiger_detail'",
                    (json.dumps(detail, ensure_ascii=False),))
        con.commit(); con.close()
        facts, spec = daily.build_lhb(DAY, daily.fetch_day_bundle(db, DAY))
        facts_by_id = {f.id: f for f in facts}
        # lhb_count 为去重后 code 数(3 股,非 4 行)
        assert facts_by_id["lhb_count"].value == 3
        # 净买 Top1 = 欢瑞世纪两行净额之和 37288799.9 + 10000000 = 0.47 亿
        assert facts_by_id["nb1_yi"].display == "+0.47亿"
        buy_card = next(c for c in spec["cards"] if c["name"] == "02_净买额居前")
        names = [r["cells"][0] for r in buy_card["table"]["rows"]]
        assert names.count("欢瑞世纪") == 1  # Top 表不重复
        assert len(names) == len(set(names))

    def test_rerun_rendered_topic_rejected(self, env):
        # Important-2:同日重跑时 topic 已 rendered/queued → 拒绝覆写;drafting/validated 可重跑
        root, db = env
        daily.generate("lhb", DAY, root, None, db)
        conn = ledger.connect(None)  # CARDGEN_LEDGER_DB 由 env fixture 注入
        try:
            ledger.set_status(conn, f"龙虎榜/{DAY}", "rendered")
        finally:
            conn.close()
        with pytest.raises(RuntimeError, match="拒绝覆写"):
            daily.generate("lhb", DAY, root, None, db)

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


class TestRealdataEdgecases:
    """2026-09-02 真实首跑暴露的边缘:转债混榜与 pct 尾零。"""

    def test_pct_trailing_zero_consistent(self):
        from decimal import Decimal
        v, disp = daily._pct_signed(57.30)
        assert v == "57.3" and disp == "+57.3%"
        # display 必须以数字边界包含 facts 序列化后的 value 形态(57.3,而非 57.30)
        from davis_analyzer.cardgen.facts import check_facts
        f = daily._fact("p", v, "%", disp, DAY, "r")
        assert check_facts([f]) == []
        v2, disp2 = daily._pct_signed(-2.5)
        assert (v2, disp2) == ("2.5", "-2.5%")

    def test_bond_rows_excluded(self, tmp_path):
        db = _mk_db(tmp_path)
        con = sqlite3.connect(db)
        row = con.execute("SELECT data_json FROM daily_data WHERE data_type='dragon_tiger_detail'").fetchone()
        detail = json.loads(row[0])
        detail.append({"code": "113XXX.SH", "name": "震裕转02", "reason": "日换手率达到20%",
                       "change_pct": 57.3, "net_buy_amount": 500000000.0,
                       "buy_amount": 6e8, "sell_amount": 1e8, "list_date": DAY.replace("-", "")})
        con.execute("UPDATE daily_data SET data_json=? WHERE data_type='dragon_tiger_detail'",
                    (json.dumps(detail, ensure_ascii=False),))
        con.commit(); con.close()
        facts, spec = daily.build_lhb(DAY, daily.fetch_day_bundle(db, DAY))
        texts = json.dumps(spec, ensure_ascii=False)
        assert "震裕转" not in texts  # 转债剔除后名字数字不进卡
        assert "57.3%" not in texts or "震裕" not in texts

    def test_publish_copy_compliance_clean(self):
        """发稿文案:敏感词全表+诱导句式零命中,含免责,正文无阿拉伯数字(数字留给卡片)。"""
        import re as _re
        from davis_analyzer.cardgen.compliance import INDUCEMENT_PATTERNS, load_words
        words = load_words()
        for kind in ("ladder", "lhb"):
            c = daily.publish_copy(kind, DAY)
            blob = c["title"] + c["body"] + c["tags"]
            hit = [w for w in words if w in blob]
            assert not hit, f"{kind} 发稿文案命中敏感词: {hit}"
            for pat, _desc in INDUCEMENT_PATTERNS:
                assert not pat.search(blob), f"{kind} 发稿文案命中诱导句式"
            assert "不构成投资建议" in c["body"]
            assert not _re.search(r"\d", c["body"]), "正文不得含数字(发稿层不走数字闸)"
            assert c["title"].startswith(DAY[5:])  # 09-02 日期前缀

    def test_insight_library_compliance_clean(self):
        """洞察库全量:零数字、敏感词全表与诱导句式零命中(入库预审由测试强制)。"""
        import re as _re
        from davis_analyzer.cardgen.compliance import INDUCEMENT_PATTERNS, load_words
        words = load_words()
        lib = [daily._LADDER_DEFAULT_INSIGHT, daily._LHB_DEFAULT_INSIGHT]
        # 连同选择器产出的全部可能句子:构造各形态 bundle 抽取
        bundles = [
            {"pool": [{}]*10, "broken": [{}]*5, "prev_boards_max": 6,
             "boards": [{"board_count": 4, "stocks": []}], "lhb_detail": [], "institutional": []},
            {"pool": [{}]*10, "broken": [{}]*1, "prev_boards_max": 3,
             "boards": [{"board_count": 6, "stocks": []}], "lhb_detail": [], "institutional": []},
            {"pool": [{}]*10, "broken": [], "prev_boards_max": None,
             "boards": [{"board_count": 3, "stocks": []}], "lhb_detail": [], "institutional": []},
        ]
        for b in bundles:
            lib += daily.ladder_insights(b)
            lib += daily.lhb_insights(b)
        lhb_b = dict(bundles[0])
        lhb_b["lhb_detail"] = [{"code": f"{i:06d}.SZ", "net_buy_amount": v} for i, v in enumerate([1e8]*5)]
        lhb_b["boards"] = [{"board_count": 3, "stocks": [{"code": f"{i:06d}.SZ", "name": "X"} for i in range(5)]}]
        lhb_b["institutional"] = [{"inst_name": "机构专用", "net_amount": 1e8}]
        lib += daily.lhb_insights(lhb_b)
        for text in lib:
            assert not _re.search(r"\d", text), f"见解句含数字: {text}"
            hit = [w for w in words if w in text]
            assert not hit, f"见解句命中敏感词{hit}: {text}"
            for pat, _d in INDUCEMENT_PATTERNS:
                assert not pat.search(text), f"见解句命中诱导句式: {text}"

    def test_ladder_insight_selection_by_pattern(self):
        b = {"pool": [{}]*40, "broken": [{}]*12, "prev_boards_max": 7,
             "boards": [{"board_count": 5, "stocks": []}], "lhb_detail": [], "institutional": []}
        picks = daily.ladder_insights(b)
        assert any("抗跌" in p for p in picks)          # 高度回落
        assert any("封板质量" in p for p in picks)       # 炸板率 12/40>=30%
        b2 = {"pool": [{}]*40, "broken": [{}]*2, "prev_boards_max": 3,
              "boards": [{"board_count": 6, "stocks": []}], "lhb_detail": [], "institutional": []}
        assert any("晋级" in p for p in daily.ladder_insights(b2))

    def test_lhb_insight_selection_by_pattern(self):
        b = {"pool": [], "broken": [], "boards": [],
             "lhb_detail": [{"code": "000001.SZ", "net_buy_amount": -1e8}],
             "institutional": [{"inst_name": "机构专用", "net_amount": 2e8}]}
        picks = daily.lhb_insights(b)
        assert any("机构口径" in p for p in picks)       # 机构净流入优先
        b2 = dict(b, institutional=[])
        picks2 = daily.lhb_insights(b2)
        assert any("净流出" in p for p in picks2)        # 榜单净流出

    def test_publish_copy_with_bundle_appends_insight(self):
        b = {"pool": [{}]*40, "broken": [{}]*12, "prev_boards_max": 7,
             "boards": [{"board_count": 5, "stocks": []}], "lhb_detail": [], "institutional": []}
        c = daily.publish_copy("ladder", DAY, b)
        assert "盘后观察" in c["body"] and "抗跌" in c["body"]
        assert "不构成投资建议" in c["body"]
