"""cninfo 巨潮两级入库测试：规则匹配/重放/拉取(mock Session)——不发真请求."""

from __future__ import annotations

import sqlite3

import pytest

from davis_analyzer.systems.surge import cninfo, db


@pytest.fixture()
def mem_conn():
    conn = sqlite3.connect(":memory:")
    db.ensure_tables(conn)
    yield conn
    conn.close()


def test_apply_rules_match_types():
    assert cninfo.apply_rules("关于终止发行股份购买资产的公告") == [("ma_halt", "negative")]
    assert cninfo.apply_rules("重大资产重组报告书(草案)") == [("ma", "positive")]
    assert cninfo.apply_rules("关于发行股份购买资产并募集配套资金的公告") == [("ma", "positive")]
    assert cninfo.apply_rules("向特定对象发行股票预案") == [("refinance", "negative")]
    assert cninfo.apply_rules("关于收到中国证监会立案告知书的公告") == [("distress", "negative")]
    assert cninfo.apply_rules("关于重大资产出售暨关联交易的公告") == [("divest", "neutral")]


def test_apply_rules_halt_priority_over_ma():
    # 同一标题同时含「终止」「重组」→ 终止类必须赢(规则表首列优先)
    got = cninfo.apply_rules("关于终止重大资产重组事项的公告")
    assert got == [("ma_halt", "negative")]


def test_apply_rules_routine_passes():
    assert cninfo.apply_rules("2026年半年度报告") == []
    assert cninfo.apply_rules("") == []


def test_apply_rules_strips_highlight_tags():
    assert cninfo.apply_rules("<em>重大资产重组</em>报告书") == [("ma", "positive")]


def test_replay_rules_rebuilds(mem_conn):
    mem_conn.executemany(
        "INSERT INTO cninfo_announcement VALUES (?,?,?,?)",
        [("000001.SZ", "20260801", "重大资产重组报告书", 0),
         ("000002.SZ", "20260901", "向特定对象发行股票预案", 0),
         ("000003.SZ", "20260901", "2026年半年度报告", 0)])
    mem_conn.commit()
    n = cninfo.replay_rules(mem_conn)
    assert n == 2  # 常规公告不命中
    rows = sorted(mem_conn.execute(
        "SELECT ts_code, event_type FROM major_events").fetchall())
    assert rows == [("000001.SZ", "ma"), ("000002.SZ", "refinance")]
    # 幂等: 重放不重复
    assert cninfo.replay_rules(mem_conn) == 2
    assert mem_conn.execute("SELECT COUNT(*) FROM major_events").fetchone()[0] == 2


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    """顺序消费预设响应;记录请求."""

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.requests: list[tuple] = []

    def post(self, url, data=None, headers=None, timeout=None, **kw):
        self.requests.append((url, dict(data or {})))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Resp(item)


def test_fetch_org_id_ok():
    s = FakeSession([[{"code": "000001", "orgId": "gssz0000001"}]])
    assert cninfo.fetch_org_id(s, "000001") == "gssz0000001"


def test_fetch_org_id_failure_returns_none():
    s = FakeSession([RuntimeError("network down")])
    assert cninfo.fetch_org_id(s, "000001") is None


def test_fetch_announcements_pagination_and_dedup():
    page1 = {"announcements": [
        {"announcementTitle": "公告A", "announcementTime": 1756684800000},
        {"announcementTitle": "公告B", "announcementTime": 1756600000000}] * 15}
    page2 = {"announcements": [
        {"announcementTitle": "公告C", "announcementTime": 1756500000000}]}
    s = FakeSession([page1, page2])
    anns = cninfo.fetch_announcements(s, "000001", "gssz0000001",
                                      "2026-07-01", "2026-09-18")
    assert len(anns) == 31
    assert {"公告A", "公告B", "公告C"} <= {a["title"] for a in anns}
    assert all(len(a["ann_date"]) == 8 and a["ann_date"].isdigit() for a in anns)


def test_sync_cninfo_two_tier_persist(mem_conn):
    # orgId 查询 + 一页公告(含规则命中)
    ann_page = {"announcements": [
        {"announcementTitle": "重大资产重组报告书", "announcementTime": 1756684800000},
        {"announcementTitle": "2026年半年度报告", "announcementTime": 1756600000000}]}
    s = FakeSession([
        [{"code": "000001", "orgId": "gssz0000001"}],   # topSearch
        ann_page,
    ])
    stats = cninfo.sync_cninfo(mem_conn, ["000001.SZ"], "20260918", session=s)
    assert stats == {"ok": 1, "fail": 0, "events": 1}
    # 原始层全量(含未命中)
    titles = [r[0] for r in mem_conn.execute(
        "SELECT title FROM cninfo_announcement")]
    assert set(titles) == {"重大资产重组报告书", "2026年半年度报告"}
    # 规则层只含命中
    assert mem_conn.execute(
        "SELECT event_type FROM major_events").fetchall() == [("ma",)]
    # orgId 已缓存,第二次不再 topSearch
    s2 = FakeSession([ann_page])
    cninfo.sync_cninfo(mem_conn, ["000001.SZ"], "20260918", session=s2)
    assert all("topSearch" not in u for u, _ in s2.requests)


def test_sync_cninfo_org_fail_degrades(mem_conn):
    s = FakeSession([RuntimeError("down")])
    stats = cninfo.sync_cninfo(mem_conn, ["000009.SZ"], "20260918", session=s)
    assert stats["fail"] == 1 and stats["ok"] == 0


def test_fetch_announcements_skips_malformed_record():
    """安全审查A2: 单条畸形 announcementTime 跳过,不影响其余公告."""
    page = {"announcements": [
        {"announcementTitle": "公告A", "announcementTime": 1756684800000},
        {"announcementTitle": "坏记录", "announcementTime": "not-a-ts"},
        {"announcementTitle": "公告B", "announcementTime": 1756500000000}]}
    s = FakeSession([page])
    anns = cninfo.fetch_announcements(s, "000001", "gssz0000001",
                                      "2026-07-01", "2026-09-18")
    assert [a["title"] for a in anns] == ["公告A", "公告B"]
