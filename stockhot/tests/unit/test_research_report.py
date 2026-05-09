from pathlib import Path

import stockhot.research_report as rr


SAMPLE_MARKET_DATA = {
    "date": "2026-04-17",
    "gainers": [
        {"name": "N尚水", "code": "301665", "change_pct": 286.72},
        {"name": "创达新材", "code": "301000", "change_pct": 12.34},
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
        }
    ],
}


def test_build_research_report_uses_theme_and_catalyst():
    result = rr.build_research_report(
        market_data=SAMPLE_MARKET_DATA,
        theme="商业航天",
        catalyst="火箭回收试验成功",
    )

    assert result["theme"] == "商业航天"
    assert result["catalyst"] == "火箭回收试验成功"
    assert result["core_judgment"].startswith("围绕“商业航天”的公开资料催化已逐步增多")
    assert any("运载火箭总装与回收验证" in item for item in result["chain_segments"])
    assert any("铂力特" == item["name"] for item in result["targets"])
    assert any("公开资料线索" in item["reason"] for item in result["targets"])
    assert result["evidence"] is not None
    assert result["source_tiers"] is not None
    assert "一级证据" in result["source_tiers"]
    assert "# 商业航天 主题研究摘要" in result["text"]


def test_build_research_report_adds_public_evidence_section_for_commercial_space():
    result = rr.build_research_report(
        market_data=SAMPLE_MARKET_DATA,
        theme="商业航天",
        catalyst=None,
    )

    assert result["catalyst"].startswith("近期商业航天的公开催化")
    assert result["current_status"][0].startswith("2026-03-30｜新华社/新华网")
    assert "## 公开资料补充" in result["text"]
    assert "## 来源分级" in result["text"]
    assert "[一级证据]" in result["text"]
    assert "力箭二号遥一在东风商业航天创新试验区首飞成功" in result["text"]
    assert "[辅助证据] 688333 铂力特" in result["text"]
    assert "[辅助证据] 002342 巨力索具" in result["text"]


def test_build_research_report_adds_public_evidence_section_for_satellite_internet():
    result = rr.build_research_report(
        market_data=SAMPLE_MARKET_DATA,
        theme="卫星互联网",
        catalyst=None,
    )

    assert result["theme"] == "卫星互联网"
    assert result["catalyst"].startswith("近期卫星互联网的公开催化")
    assert any("低轨卫星组网与发射节奏" in item for item in result["chain_segments"])
    assert result["current_status"][0].startswith("2026-03-05｜中国政府网")
    assert "## 公开资料补充" in result["text"]
    assert "## 来源分级" in result["text"]
    assert "[一级证据] 601698 中国卫通" in result["text"]
    assert "卫星互联网进入政府工作报告" in result["text"]


def test_curated_evidence_does_not_match_broader_adjacent_aerospace_theme():
    result = rr.build_research_report(
        market_data=SAMPLE_MARKET_DATA,
        theme="航天设备",
        catalyst=None,
    )

    assert result["evidence"] is None
    assert result["catalyst"] == "当前暂无与该主题直接匹配的催化样本。"


def test_curated_evidence_does_not_match_exact_broad_aerospace_theme():
    unrelated_market_data = {
        "date": "2026-04-17",
        "gainers": [{"name": "创新药样本", "code": "300001", "change_pct": 12.0}],
        "sectors": [{"name": "医药生物", "change_pct": 3.21, "leader_stock": "创新药样本"}],
        "fund_flows": [
            {
                "name": "创新药",
                "net_inflow": 10.5,
                "source": "ths",
                "category": "industry",
                "leader_stock": "创新药样本",
            }
        ],
    }
    result = rr.build_research_report(
        market_data=unrelated_market_data,
        theme="航天",
        catalyst=None,
    )

    assert result["theme"] == "航天"
    assert result["evidence"] is None
    assert result["catalyst"] == "当前暂无与该主题直接匹配的催化样本。"


def test_alias_theme_is_normalized_to_commercial_space_when_evidence_pack_matches():
    result = rr.build_research_report(
        market_data=SAMPLE_MARKET_DATA,
        theme="火箭回收",
        catalyst=None,
    )

    assert result["theme"] == "商业航天"
    assert result["catalyst"].startswith("近期商业航天的公开催化")
    assert result["evidence"] is not None


def test_alias_theme_is_normalized_to_satellite_internet_when_evidence_pack_matches():
    result = rr.build_research_report(
        market_data=SAMPLE_MARKET_DATA,
        theme="卫星通信",
        catalyst=None,
    )

    assert result["theme"] == "卫星互联网"
    assert result["catalyst"].startswith("近期卫星互联网的公开催化")
    assert result["evidence"] is not None


def test_get_curated_theme_evidence_resolves_across_all_registered_packs():
    from stockhot.research_report.evidence import get_curated_theme_evidence

    commercial = get_curated_theme_evidence("火箭回收")
    satellite = get_curated_theme_evidence("卫星通信")

    assert commercial is not None
    assert satellite is not None
    assert commercial["theme"] == "商业航天"
    assert satellite["theme"] == "卫星互联网"


def test_build_research_report_derives_theme_from_samples():
    result = rr.build_research_report(
        market_data=SAMPLE_MARKET_DATA,
        theme=None,
        catalyst=None,
    )

    assert result["theme"] == "商业航天"
    assert (
        result["catalyst"]
        == "近期商业航天的公开催化，主要集中在可重复使用火箭验证、政策支持和发射基础设施建设。"
    )


def test_build_research_report_uses_empty_state_when_theme_does_not_match_samples():
    result = rr.build_research_report(
        market_data=SAMPLE_MARKET_DATA,
        theme="创新药",
        catalyst="新药审批进展",
    )

    assert result["theme"] == "创新药"
    assert result["core_judgment"] == "当前围绕“创新药”的样本线索有限，需继续等待更明确的验证信号。"
    assert result["chain_segments"][0] == "当前样本中尚未检出与“创新药”高度相关的板块样本。"
    assert (
        result["chain_segments"][1]
        == "第一版研报仅引用当前可核验样本，不扩展到未验证的细分产业链公司。"
    )
    assert result["current_status"] == ["当前样本中尚未检出与“创新药”高度相关的市场反馈。"]
    assert result["targets"] == []
    assert "当前样本不足，暂未筛出高置信度代表性标的。" in result["text"]


def test_build_research_report_marks_user_catalyst_as_unverified_when_theme_mismatches():
    result = rr.build_research_report(
        market_data=SAMPLE_MARKET_DATA,
        theme="创新药",
        catalyst="新药审批进展",
    )

    assert result["catalyst"] == "外部催化线索（待样本验证）：新药审批进展"
    assert result["current_status"] == ["当前样本中尚未检出与“创新药”高度相关的市场反馈。"]


def test_build_next_milestones_cleans_catalyst_punctuation():
    milestones = rr._build_next_milestones("创新药", "近期围绕“创新药”的市场关注度升温。", None)

    assert (
        milestones[1]
        == "围绕当前催化：近期围绕“创新药”的市场关注度升温，继续观察市场反馈是否扩散。"
    )


def test_build_next_milestones_uses_empty_state_wording_for_missing_catalyst_sample():
    milestones = rr._build_next_milestones("创新药", "当前暂无与该主题直接匹配的催化样本。", None)

    assert milestones[1] == "当前催化样本仍不足，先观察后续是否出现更直接的事件验证。"


def test_build_research_report_handles_mismatch_without_catalyst():
    result = rr.build_research_report(
        market_data=SAMPLE_MARKET_DATA,
        theme="创新药",
        catalyst=None,
    )

    assert result["catalyst"] == "当前暂无与该主题直接匹配的催化样本。"
    assert result["current_status"] == ["当前样本中尚未检出与“创新药”高度相关的市场反馈。"]
    assert (
        result["next_milestones"][1] == "当前催化样本仍不足，先观察后续是否出现更直接的事件验证。"
    )


def test_run_research_report_returns_no_data(monkeypatch):
    monkeypatch.setattr(rr, "get_daily_data", lambda date: {"date": date})

    result = rr.run_research_report("2026-04-17")

    assert result == {"date": "2026-04-17", "status": "no_data"}


def test_run_research_report_persists_and_writes_markdown(monkeypatch, tmp_path: Path):
    saved = []
    monkeypatch.setattr(rr, "get_daily_data", lambda date: SAMPLE_MARKET_DATA)
    monkeypatch.setattr(
        rr, "save_analysis_result", lambda date, kind, payload: saved.append((date, kind, payload))
    )
    monkeypatch.setattr(rr, "get_reports_dir_for_date", lambda date: tmp_path)

    result = rr.run_research_report(
        date="2026-04-17",
        theme="商业航天",
        catalyst="火箭回收试验成功",
    )

    report_path = tmp_path / "2026-04-17_hot_theme_research_report.md"
    assert result == {
        "date": "2026-04-17",
        "status": "success",
        "theme": "商业航天",
        "report_path": str(report_path),
    }
    assert saved and saved[0][0] == "2026-04-17"
    assert saved[0][1] == "hot_theme_report"
    assert report_path.exists()
    assert "# 商业航天 主题研究摘要" in report_path.read_text(encoding="utf-8")


def test_run_research_report_uses_hotspot_discovery_lead_theme_when_theme_omitted(
    monkeypatch, tmp_path: Path
):
    saved = []
    monkeypatch.setattr(rr, "get_daily_data", lambda date: SAMPLE_MARKET_DATA)
    monkeypatch.setattr(
        rr,
        "get_preferred_analysis_result",
        lambda date, preferred_types: {"lead_theme": "商业航天"},
    )
    monkeypatch.setattr(
        rr, "save_analysis_result", lambda date, kind, payload: saved.append((date, kind, payload))
    )
    monkeypatch.setattr(rr, "get_reports_dir_for_date", lambda date: tmp_path)

    result = rr.run_research_report(date="2026-04-17", theme=None, catalyst=None)

    assert result["theme"] == "商业航天"
    assert saved[0][2]["theme"] == "商业航天"
