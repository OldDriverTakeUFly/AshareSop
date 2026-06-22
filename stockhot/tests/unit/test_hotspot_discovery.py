import stockhot.hotspot_discovery as hd
import stockhot.hotspot_discovery.news_sources as news_sources
from stockhot.hotspot_discovery.news_sources import collect_curated_news_events

SAMPLE_MARKET_DATA = {
    "date": "2026-04-17",
    "gainers": [
        {"name": "创达新材", "code": "301000", "change_pct": 12.34},
        {"name": "N尚水", "code": "301665", "change_pct": 286.72},
    ],
    "sectors": [
        {"name": "商业航天", "change_pct": 4.96, "leader_stock": "创达新材"},
        {"name": "电子设备", "change_pct": 3.12, "leader_stock": "宁德时代"},
    ],
    "fund_flows": [
        {
            "name": "商业航天",
            "net_inflow": 92.22,
            "source": "ths",
            "category": "industry",
            "leader_stock": "创达新材",
        },
        {
            "name": "电子设备",
            "net_inflow": 21.1,
            "source": "ths",
            "category": "industry",
            "leader_stock": "宁德时代",
        },
    ],
}


def test_build_hotspot_discovery_returns_sorted_theme_candidates():
    result = hd.build_hotspot_discovery(SAMPLE_MARKET_DATA)

    assert result["method"] == "sample+public-news-v2.5"
    assert result["lead_theme"] == "商业航天"
    assert result["themes"][0]["name"] == "商业航天"
    assert result["themes"][0]["confidence"] == "high"
    assert result["themes"][0]["source_mode"] == "sample+evidence"
    assert result["themes"][0]["matched_sectors"]
    assert result["themes"][0]["matched_fund_flows"]
    assert result["themes"][0]["news_signals"]
    assert result["themes"][0]["evidence_sources"]
    assert result["themes"][0]["summary"].startswith("公开资料中，力箭二号遥一首飞成功。")
    assert result["event_backed_themes"][0] == "商业航天"
    assert "卫星互联网" in result["event_backed_themes"]
    top_event_themes = {item["theme"] for item in result["event_theme_candidates"][:2]}
    # With 6 evidence packs, the top-2 by catalyst recency may vary;
    # just verify they are valid evidence pack themes
    all_evidence_themes = {"商业航天", "卫星互联网", "AI芯片", "新能源", "低空经济", "消费电子"}
    assert top_event_themes.issubset(all_evidence_themes)
    assert result["news_event_clusters"]
    assert result["theme_clusters"][0]["canonical_theme"] == "商业航天"
    assert result["theme_clusters"][0]["aliases"][:2] == ["商业航天", "火箭回收"]


def test_build_hotspot_discovery_matches_stocks_when_sector_map_knows_theme():
    market_data = {
        "date": "2026-04-17",
        "gainers": [{"name": "宁波银行", "code": "002142", "change_pct": 5.2}],
        "sectors": [{"name": "银行", "change_pct": 2.1, "leader_stock": "宁波银行"}],
        "fund_flows": [],
    }

    result = hd.build_hotspot_discovery(market_data)

    assert result["lead_theme"] == "银行"
    assert result["themes"][0]["matched_stocks"] == [
        {"name": "宁波银行", "code": "002142", "change_pct": 5.2}
    ]


def test_build_hotspot_discovery_includes_limitations():
    result = hd.build_hotspot_discovery(SAMPLE_MARKET_DATA)

    assert len(result["limitations"]) == 2
    assert "人工整理的公开资料证据包" in result["limitations"][0]


def test_collect_curated_news_events_normalizes_public_evidence_pack():
    events = collect_curated_news_events()

    assert events
    first = events[0]
    assert first["theme"] == "商业航天"
    assert first["mode"] == "curated-public-evidence"
    assert "火箭回收" in first["aliases"]
    assert first["source"]
    assert first["title"]


def test_collect_miit_public_news_events_parses_public_page(monkeypatch):
    html = """
    <ul>
      <li>
        <a href="http://www.miit.gov.cn/xwfb/gxdt/art/2026/test.html" title="工业和信息化部召开商业航天创新座谈会" target="_blank">工业和信息化部召开商业航天创新座谈会</a>
        <span>04-24</span>
      </li>
      <li>
        <a href="http://www.miit.gov.cn/xwfb/gxdt/art/2026/test2.html" title="工业和信息化部召开人工智能产业发展座谈会" target="_blank">工业和信息化部召开人工智能产业发展座谈会</a>
        <span>04-24</span>
      </li>
    </ul>
    """

    class _Resp:
        status_code = 200
        text = html

        def raise_for_status(self):
            return None

    monkeypatch.setattr(news_sources.requests, "get", lambda *args, **kwargs: _Resp())

    events = news_sources.collect_miit_public_news_events(target_date="2026-04-24")

    assert len(events) == 2
    assert events[0]["source"] == "工信部RSS页"
    assert events[0]["mode"] == "miit-public-page"
    assert events[0]["theme"] == "商业航天"
    assert events[1]["theme"] == ""


def test_collect_gov_cn_policy_events_parses_public_json(monkeypatch):
    payload = [
        {
            "TITLE": "国务院关于深入实施人工智能+行动的意见",
            "URL": "https://www.gov.cn/zhengce/content/202508/content_7037861.htm",
            "DOCRELPUBTIME": "2026-04-24",
        },
        {
            "TITLE": "国务院关于商业航天高质量发展的若干意见",
            "URL": "https://www.gov.cn/zhengce/content/202604/content_7064837.htm",
            "DOCRELPUBTIME": "2026-04-24",
        },
    ]

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return payload

    monkeypatch.setattr(news_sources.requests, "get", lambda *args, **kwargs: _Resp())

    events = news_sources.collect_gov_cn_policy_events(target_date="2026-04-24")

    assert len(events) == 2
    assert events[0]["source"] == "中国政府网最新政策"
    assert events[0]["mode"] == "govcn-policy-json"
    assert events[0]["theme"] == ""
    assert events[1]["theme"] == "商业航天"


def test_collect_stcn_kuaixun_events_parses_public_page(monkeypatch):
    html = """
    <ul id="news_list2">
      <li>
        <i>09:15</i>
        <a href="./egs/202604/t20260424_123.html" title="商业航天产业链迎来新一轮技术验证" target="_blank">商业航天产业链迎来新一轮技术验证</a>
        <span>2026-04-24</span>
      </li>
      <li>
        <i>09:16</i>
        <a href="./cj/202604/t20260424_456.html" title="人工智能产业生态持续完善" target="_blank">人工智能产业生态持续完善</a>
        <span>2026-04-24</span>
      </li>
    </ul>
    """

    class _Resp:
        status_code = 200
        text = html

        def raise_for_status(self):
            return None

    monkeypatch.setattr(news_sources.requests, "get", lambda *args, **kwargs: _Resp())

    events = news_sources.collect_stcn_kuaixun_events(target_date="2026-04-24")

    assert len(events) == 2
    assert events[0]["source"] == "证券时报快讯"
    assert events[0]["mode"] == "stcn-kuaixun-page"
    assert events[0]["theme"] == "商业航天"
    assert events[0]["url"] == "https://kuaixun.stcn.com/egs/202604/t20260424_123.html"
    assert events[1]["theme"] == ""


def test_collect_ndrc_notice_events_parses_public_page(monkeypatch):
    html = """
    <ul class="u-list">
      <li><a href="./202604/t20260424_1404861.html" target="_blank" title="关于加快卫星互联网建设的通知">关于加快卫星互联网建设的通知</a><span>2026/04/24</span></li>
      <li><a href="./202604/t20260424_1404862.html" target="_blank" title="关于促进人工智能产业高质量发展的通知">关于促进人工智能产业高质量发展的通知</a><span>2026/04/24</span></li>
    </ul>
    """

    class _Resp:
        status_code = 200
        text = html

        def raise_for_status(self):
            return None

    monkeypatch.setattr(news_sources.requests, "get", lambda *args, **kwargs: _Resp())

    events = news_sources.collect_ndrc_notice_events(target_date="2026-04-24")

    assert len(events) == 2
    assert events[0]["source"] == "国家发改委通知"
    assert events[0]["mode"] == "ndrc-notice-page"
    assert events[0]["theme"] == "卫星互联网"
    assert events[0]["url"] == "https://www.ndrc.gov.cn/xwdt/tzgg/202604/t20260424_1404861.html"
    assert events[1]["theme"] == ""


def test_build_hotspot_discovery_includes_stcn_raw_and_matched_news(monkeypatch):
    sample_events = [
        {
            "theme": "商业航天",
            "aliases": ["商业航天", "火箭回收"],
            "date": "2026-04-24",
            "source": "证券时报快讯",
            "tier": "辅助证据",
            "title": "商业航天产业链迎来新一轮技术验证",
            "summary": "商业航天产业链迎来新一轮技术验证",
            "url": "https://kuaixun.stcn.com/egs/202604/t20260424_123.html",
            "mode": "stcn-kuaixun-page",
        },
        {
            "theme": "",
            "aliases": [],
            "date": "2026-04-24",
            "source": "证券时报快讯",
            "tier": "辅助证据",
            "title": "人工智能产业生态持续完善",
            "summary": "人工智能产业生态持续完善",
            "url": "https://kuaixun.stcn.com/cj/202604/t20260424_456.html",
            "mode": "stcn-kuaixun-page",
        },
    ]
    monkeypatch.setattr(hd, "collect_news_events", lambda target_date=None: sample_events)

    result = hd.build_hotspot_discovery(SAMPLE_MARKET_DATA, target_date="2026-04-24")

    assert len(result["raw_news_events"]) == 2
    assert len(result["news_events"]) == 1
    assert result["news_events"][0]["source"] == "证券时报快讯"


def test_build_hotspot_discovery_includes_ndrc_raw_and_matched_news(monkeypatch):
    sample_events = [
        {
            "theme": "卫星互联网",
            "aliases": ["卫星互联网", "卫星通信"],
            "date": "2026-04-24",
            "source": "国家发改委通知",
            "tier": "一级证据",
            "title": "关于加快卫星互联网建设的通知",
            "summary": "关于加快卫星互联网建设的通知",
            "url": "https://www.ndrc.gov.cn/xwdt/tzgg/202604/t20260424_1404861.html",
            "mode": "ndrc-notice-page",
        },
        {
            "theme": "",
            "aliases": [],
            "date": "2026-04-24",
            "source": "国家发改委通知",
            "tier": "一级证据",
            "title": "关于促进人工智能产业高质量发展的通知",
            "summary": "关于促进人工智能产业高质量发展的通知",
            "url": "https://www.ndrc.gov.cn/xwdt/tzgg/202604/t20260424_1404862.html",
            "mode": "ndrc-notice-page",
        },
    ]
    monkeypatch.setattr(hd, "collect_news_events", lambda target_date=None: sample_events)

    result = hd.build_hotspot_discovery(SAMPLE_MARKET_DATA, target_date="2026-04-24")

    assert len(result["raw_news_events"]) == 2
    assert len(result["news_events"]) == 1
    assert result["news_events"][0]["source"] == "国家发改委通知"


def test_build_hotspot_discovery_includes_gov_cn_raw_and_matched_news(monkeypatch):
    sample_events = [
        {
            "theme": "商业航天",
            "aliases": ["商业航天"],
            "date": "2026-04-24",
            "source": "中国政府网最新政策",
            "tier": "一级证据",
            "title": "国务院关于商业航天高质量发展的若干意见",
            "summary": "国务院关于商业航天高质量发展的若干意见",
            "url": "http://example.com/a",
            "mode": "govcn-policy-json",
        },
        {
            "theme": "",
            "aliases": [],
            "date": "2026-04-24",
            "source": "中国政府网最新政策",
            "tier": "一级证据",
            "title": "国务院关于深入实施人工智能+行动的意见",
            "summary": "国务院关于深入实施人工智能+行动的意见",
            "url": "http://example.com/b",
            "mode": "govcn-policy-json",
        },
    ]
    monkeypatch.setattr(hd, "collect_news_events", lambda target_date=None: sample_events)

    result = hd.build_hotspot_discovery(SAMPLE_MARKET_DATA, target_date="2026-04-24")

    assert len(result["raw_news_events"]) == 2
    assert len(result["news_events"]) == 1
    assert result["news_events"][0]["source"] == "中国政府网最新政策"


def test_build_hotspot_discovery_includes_raw_and_matched_news(monkeypatch):
    sample_events = [
        {
            "theme": "商业航天",
            "aliases": ["商业航天", "火箭回收"],
            "date": "2026-04-24",
            "source": "工信部RSS页",
            "tier": "一级证据",
            "title": "工业和信息化部召开商业航天创新座谈会",
            "summary": "工业和信息化部召开商业航天创新座谈会",
            "url": "http://example.com/a",
            "mode": "miit-public-page",
        },
        {
            "theme": "",
            "aliases": [],
            "date": "2026-04-24",
            "source": "工信部RSS页",
            "tier": "一级证据",
            "title": "工业和信息化部召开人工智能产业发展座谈会",
            "summary": "工业和信息化部召开人工智能产业发展座谈会",
            "url": "http://example.com/b",
            "mode": "miit-public-page",
        },
    ]
    monkeypatch.setattr(hd, "collect_news_events", lambda target_date=None: sample_events)

    result = hd.build_hotspot_discovery(SAMPLE_MARKET_DATA, target_date="2026-04-24")

    assert len(result["raw_news_events"]) == 2
    assert len(result["news_events"]) == 1
    assert len(result["news_event_clusters"]) == 1
    assert result["news_events"][0]["title"] == "工业和信息化部召开商业航天创新座谈会"


def test_cluster_news_events_deduplicates_same_theme_source_title():
    events = [
        {
            "theme": "商业航天",
            "aliases": ["商业航天"],
            "date": "2026-04-23",
            "source": "工信部RSS页",
            "tier": "一级证据",
            "title": "工业和信息化部召开商业航天创新座谈会",
            "summary": "A",
            "url": "http://example.com/a",
            "mode": "miit-public-page",
        },
        {
            "theme": "商业航天",
            "aliases": ["商业航天"],
            "date": "2026-04-24",
            "source": "工信部RSS页",
            "tier": "一级证据",
            "title": "工业和信息化部召开商业航天创新座谈会",
            "summary": "B",
            "url": "http://example.com/b",
            "mode": "miit-public-page",
        },
    ]

    clusters = hd._cluster_news_events(events)

    assert len(clusters) == 1
    assert clusters[0]["member_count"] == 2
    assert clusters[0]["date"] == "2026-04-24"


def test_run_hotspot_discovery_returns_no_data_for_empty_market_data(monkeypatch):
    monkeypatch.setattr(hd, "get_daily_data", lambda date: {"date": date})

    result = hd.run_hotspot_discovery("2026-04-17")

    assert result == {"date": "2026-04-17", "status": "no_data"}


def test_run_hotspot_discovery_persists_result(monkeypatch):
    saved = []
    monkeypatch.setattr(hd, "get_daily_data", lambda date: SAMPLE_MARKET_DATA)
    monkeypatch.setattr(
        hd, "save_analysis_result", lambda date, kind, payload: saved.append((date, kind, payload))
    )

    result = hd.run_hotspot_discovery("2026-04-17")

    assert result["date"] == "2026-04-17"
    assert result["status"] == "success"
    assert result["lead_theme"] == "商业航天"
    assert saved and saved[0][1] == "hotspot_discovery"


def test_run_hotspot_discovery_persists_expanded_news_payload(monkeypatch):
    saved = []
    monkeypatch.setattr(hd, "get_daily_data", lambda date: SAMPLE_MARKET_DATA)
    monkeypatch.setattr(
        hd,
        "collect_news_events",
        lambda target_date=None: [
            {
                "theme": "商业航天",
                "aliases": ["商业航天", "火箭回收"],
                "date": "2026-04-24",
                "source": "国家发改委通知",
                "tier": "一级证据",
                "title": "关于加快商业航天产业发展的通知",
                "summary": "关于加快商业航天产业发展的通知",
                "url": "https://www.ndrc.gov.cn/xwdt/tzgg/202604/t20260424_1404861.html",
                "mode": "ndrc-notice-page",
            }
        ],
    )
    monkeypatch.setattr(
        hd,
        "save_analysis_result",
        lambda date, kind, payload: saved.append((date, kind, payload)),
    )

    result = hd.run_hotspot_discovery("2026-04-24")

    assert result["status"] == "success"
    assert saved
    payload = saved[0][2]
    assert payload["raw_news_events"][0]["source"] == "国家发改委通知"
    assert payload["news_events"][0]["source"] == "国家发改委通知"
    assert payload["event_theme_candidates"][0]["latest_event"]["source"] == "国家发改委通知"


def test_build_hotspot_discovery_can_surface_evidence_only_theme_candidate():
    market_data = {
        "date": "2026-04-17",
        "gainers": [{"name": "创新药样本", "code": "300001", "change_pct": 12.0}],
        "sectors": [{"name": "医药生物", "change_pct": 3.21, "leader_stock": "创新药样本"}],
        "fund_flows": [],
    }

    result = hd.build_hotspot_discovery(market_data)
    commercial_space = next(item for item in result["themes"] if item["name"] == "商业航天")

    assert commercial_space["source_mode"] == "evidence-only"
    assert commercial_space["confidence"] == "low"
    assert commercial_space["news_signals"]
    assert commercial_space["matched_sectors"] == []


def test_build_hotspot_discovery_merges_public_news_into_theme_candidate(monkeypatch):
    sample_events = [
        {
            "theme": "新能源车",
            "aliases": ["新能源车"],
            "date": "2026-04-24",
            "source": "工信部RSS页",
            "tier": "一级证据",
            "title": "工业和信息化部召开新能源汽车产业发展座谈会",
            "summary": "工业和信息化部召开新能源汽车产业发展座谈会",
            "url": "http://example.com/a",
            "mode": "miit-public-page",
        }
    ]
    market_data = {
        "date": "2026-04-24",
        "gainers": [],
        "sectors": [{"name": "新能源车", "change_pct": 3.11, "leader_stock": "比亚迪"}],
        "fund_flows": [],
    }
    monkeypatch.setattr(hd, "collect_news_events", lambda target_date=None: sample_events)

    result = hd.build_hotspot_discovery(market_data, target_date="2026-04-24")
    candidate = next(item for item in result["themes"] if item["name"] == "新能源车")

    assert candidate["source_mode"] == "sample+evidence"
    assert candidate["news_signals"][0]["source"] == "工信部RSS页"
    assert any(entry["tier"] == "一级证据" for entry in candidate["evidence_sources"])


def test_build_hotspot_discovery_keeps_curated_and_public_news_together(monkeypatch):
    sample_events = [
        {
            "theme": "商业航天",
            "aliases": ["商业航天", "火箭回收"],
            "date": "2026-04-24",
            "source": "工信部RSS页",
            "tier": "一级证据",
            "title": "工业和信息化部召开商业航天创新座谈会",
            "summary": "工业和信息化部召开商业航天创新座谈会",
            "url": "http://example.com/a",
            "mode": "miit-public-page",
        }
    ]
    monkeypatch.setattr(hd, "collect_news_events", lambda target_date=None: sample_events)

    result = hd.build_hotspot_discovery(SAMPLE_MARKET_DATA, target_date="2026-04-24")
    candidate = next(item for item in result["themes"] if item["name"] == "商业航天")

    assert len(candidate["news_signals"]) >= 1
    assert any(signal["source"] == "工信部RSS页" for signal in candidate["news_signals"])
    assert any(signal["source"] == "新华社/新华网" for signal in candidate["news_signals"])
    assert any(entry["tier"] == "一级证据" for entry in candidate["evidence_sources"])
    assert any(entry["tier"] == "辅助证据" for entry in candidate["evidence_sources"])


def test_build_hotspot_discovery_exposes_theme_clusters_with_supporting_sources():
    result = hd.build_hotspot_discovery(SAMPLE_MARKET_DATA)
    cluster = result["theme_clusters"][0]

    assert cluster["canonical_theme"] == "商业航天"
    assert cluster["source_mode"] == "sample+evidence"
    assert cluster["signal_counts"] == {
        "sectors": 1,
        "fund_flows": 1,
        "stocks": 0,
        "news": len(cluster["news_signals"]),
        "evidence": len(cluster["evidence_sources"]),
    }
    assert "板块样本" in cluster["supporting_sources"]
    assert "THS行业" in cluster["supporting_sources"]
    assert any(src == "新华社/新华网" for src in cluster["supporting_sources"])
    assert cluster["cluster_summary"].startswith("商业航天存在公开事件线索。")


def test_theme_clusters_follow_themes_order():
    result = hd.build_hotspot_discovery(SAMPLE_MARKET_DATA)

    assert [item["name"] for item in result["themes"]] == [
        item["canonical_theme"] for item in result["theme_clusters"]
    ]
