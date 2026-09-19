# davis_analyzer/tests/test_recap_selector.py
"""recap 选片:戏剧性评分/多样性/教育性/冰点降级/facts 装配。"""
from __future__ import annotations

from davis_analyzer.systems.recap.selector import score_day, select_candidates


def _bundle(**over):
    b = {
        "pool": [
            {"ts_code": "605577.SH", "name": "龙版传媒", "sector": "出版", "change_pct": 9.97,
             "consecutive_boards": 5, "broken_count": 3, "first_seal_time": "09:47:00",
             "last_seal_time": "14:46:00", "turnover_rate": 11.9},
            {"ts_code": "001216.SZ", "name": "华瓷股份", "sector": "陶瓷", "change_pct": 10.0,
             "consecutive_boards": 1, "broken_count": 0, "first_seal_time": "09:35:00",
             "last_seal_time": "09:35:00", "turnover_rate": 3.0},
            {"ts_code": "600001.SH", "name": "出版A", "sector": "出版", "change_pct": 10.0,
             "consecutive_boards": 1, "broken_count": 0, "first_seal_time": "10:00:00",
             "last_seal_time": "10:00:00", "turnover_rate": 5.0},
            {"ts_code": "600002.SH", "name": "出版B", "sector": "出版", "change_pct": 10.0,
             "consecutive_boards": 1, "broken_count": 0, "first_seal_time": "10:05:00",
             "last_seal_time": "10:05:00", "turnover_rate": 5.0},
        ],
        "broken": [], "down": [],
        "boards": [{"board_count": 5, "stocks": [{"code": "605577.SH", "name": "龙版传媒"}]},
                   {"board_count": 1, "stocks": [{"code": "001216.SZ"}, {"code": "600001.SH"}]}],
        "lhb_codes": {"605577.SH"}, "lhb_detail": [{"code": "605577.SH", "net_buy_amount": 1.2e8}],
        "brokers": [{"broker_name": "X营业部", "net_amount": 2.0e8}],
        "index": [{"code": "000001.SH", "name": "上证指数", "close": 3875.6, "pct_chg": -0.411}],
        "amplitude_top": [{"ts_code": "605577.SH", "amplitude_pct": 18.5}],
        "breadth": {"up": 3200, "down": 1900}, "names": {}, "limit_up_count": 3,
    }
    b.update(over)
    return b


def test_score_day_ranks_drama():
    cands = score_day(_bundle())
    assert cands[0].ts_code == "605577.SH"      # 5板+3炸回封+尾盘回封+龙虎榜=最高分
    top = cands[0]
    kinds = {e.kind for e in top.events}
    assert "reseal" in kinds and "ladder" in kinds and "lhb" in kinds
    assert top.replay_start < "09:47:00" < top.replay_end  # 回放窗覆盖首封时间


def test_select_diversity_and_educational():
    picked = select_candidates(_bundle(), max_count=2, per_sector_cap=1)
    sectors = [c.sector for c in picked]
    assert len(sectors) == len(set(sectors))    # 同板块上限 1
    # 出版板块 3 家涨停=板块效应,605577 educational=True 且必入选
    assert any(c.ts_code == "605577.SH" and c.educational for c in picked)


def test_ice_day_returns_empty():
    empty = _bundle(pool=[], broken=[], boards=[], amplitude_top=[], limit_up_count=0)
    assert select_candidates(empty) == []
    assert score_day(empty) == []


def test_broken_pool_scores():
    b = _bundle(pool=[], boards=[], broken=[
        {"ts_code": "688296.SH", "name": "和达科技", "sector": "软件", "broken_count": 2}])
    cands = score_day(b)
    assert cands and cands[0].ts_code == "688296.SH"
    assert cands[0].events[0].kind == "broken"


def test_facts_attached():
    top = score_day(_bundle())[0]
    ids = {f["id"] for f in top.facts}
    assert "605577.SH_boards" in ids and "605577.SH_broken" in ids
