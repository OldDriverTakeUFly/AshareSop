import stockhot.ai_analyzer as aa

SAMPLE_DATA = {
    "date": "2026-04-17",
    "gainers": [{"name": "N尚水", "code": "301665", "change_pct": 286.72}],
    "losers": [{"name": "样本股", "code": "000001", "change_pct": -3.21}],
    "sectors": [{"name": "电子设备", "change_pct": 4.96}],
    "fund_flows": [
        {"name": "通信设备", "net_inflow": 92.22, "source": "ths", "category": "industry"}
    ],
}


def test_run_analysis_returns_no_data_when_daily_data_missing(monkeypatch):
    monkeypatch.setattr(aa, "get_daily_data", lambda date: {"date": date})

    result = aa.run_analysis("2026-04-17")

    assert result == {"date": "2026-04-17", "status": "no_data"}


def test_run_analysis_returns_no_data_when_daily_data_is_none(monkeypatch):
    monkeypatch.setattr(aa, "get_daily_data", lambda date: None)

    result = aa.run_analysis("2026-04-17")

    assert result == {"date": "2026-04-17", "status": "no_data"}


def test_run_analysis_returns_no_data_when_market_lists_are_empty(monkeypatch):
    monkeypatch.setattr(
        aa,
        "get_daily_data",
        lambda date: {"date": date, "gainers": [], "losers": [], "sectors": [], "fund_flows": []},
    )

    result = aa.run_analysis("2026-04-17")

    assert result == {"date": "2026-04-17", "status": "no_data"}


def test_analyze_hotspots_uses_local_fallback_when_ai_unavailable(monkeypatch):
    monkeypatch.setattr(aa, "call_ai_optional", lambda prompt: None)

    result = aa.analyze_hotspots(SAMPLE_DATA)

    assert result["hotspots"] == []
    assert "N尚水" in result["reasons"][0]
    assert "电子设备" in result["reasons"][1]
    assert "当前THS行业资金样本中，通信设备净流入约92.22亿。" == result["fund_flow_analysis"]


def test_analyze_hotspots_uses_ai_success_path_but_filters_to_real_sector_names(monkeypatch):
    monkeypatch.setattr(
        aa,
        "call_ai_optional",
        lambda prompt: "电子设备仍在样本中领跑，军工走强需注意风险，注意高位波动。",
    )

    result = aa.analyze_hotspots(SAMPLE_DATA)

    assert result["hotspots"] == ["电子设备"]
    assert result["reasons"][0].startswith("个股样本中，N尚水")
    assert result["risk_warnings"] == ["电子设备仍在样本中领跑，军工走强需注意风险，注意高位波动。"]
    assert result["raw_analysis"]


def test_generate_daily_report_uses_local_fallback_when_ai_unavailable(monkeypatch):
    monkeypatch.setattr(aa, "call_ai_optional", lambda prompt: None)

    report = aa.generate_daily_report(SAMPLE_DATA, aa._local_hotspot_analysis(SAMPLE_DATA))

    assert "## 市场复盘摘要" in report
    assert "个股端，N尚水涨幅+286.72%" in report
    assert "板块端，电子设备涨幅+4.96%" in report
    assert "THS行业资金样本中，通信设备净流入约92.22亿。" in report


def test_generate_daily_report_prefers_hotspot_discovery_labels():
    analysis = {
        "lead_theme": "商业航天",
        "themes": [{"name": "商业航天"}, {"name": "卫星互联网"}],
        "reasons": ["观察依据"],
        "risk_warnings": [],
    }

    report = aa.generate_daily_report(SAMPLE_DATA, analysis)

    assert "热点线索可先看：商业航天、卫星互联网。" in report


def test_run_analysis_prefers_hotspot_discovery_for_report_generation(monkeypatch):
    saved = []
    monkeypatch.setattr(aa, "get_daily_data", lambda date: SAMPLE_DATA)
    monkeypatch.setattr(
        aa,
        "analyze_hotspots",
        lambda data: {"hotspots": [], "reasons": [], "risk_warnings": [], "raw_analysis": ""},
    )
    monkeypatch.setattr(
        aa,
        "get_preferred_analysis_result",
        lambda date, types: {"lead_theme": "商业航天", "themes": [{"name": "商业航天"}]},
    )
    monkeypatch.setattr(
        aa, "generate_daily_report", lambda data, analysis: f"主题={analysis.get('lead_theme')}"
    )
    monkeypatch.setattr(
        aa, "save_analysis_result", lambda date, kind, result: saved.append((date, kind, result))
    )

    result = aa.run_analysis("2026-04-17")

    assert result == {"date": "2026-04-17", "status": "success"}
    assert saved[-1] == ("2026-04-17", "report", {"text": "主题=商业航天"})


def test_run_analysis_persists_hotspots_and_report(monkeypatch):
    saved = []
    monkeypatch.setattr(aa, "get_daily_data", lambda date: SAMPLE_DATA)
    monkeypatch.setattr(
        aa,
        "analyze_hotspots",
        lambda data: {
            "hotspots": [],
            "reasons": ["观察依据"],
            "fund_flow_analysis": "样本",
            "risk_warnings": [],
            "raw_analysis": "",
        },
    )
    monkeypatch.setattr(aa, "generate_daily_report", lambda data, analysis: "报告正文")
    monkeypatch.setattr(
        aa, "save_analysis_result", lambda date, kind, result: saved.append((date, kind, result))
    )

    result = aa.run_analysis("2026-04-17")

    assert result == {"date": "2026-04-17", "status": "success"}
    assert saved == [
        (
            "2026-04-17",
            "hotspots",
            {
                "hotspots": [],
                "reasons": ["观察依据"],
                "fund_flow_analysis": "样本",
                "risk_warnings": [],
                "raw_analysis": "",
            },
        ),
        ("2026-04-17", "report", {"text": "报告正文"}),
    ]
