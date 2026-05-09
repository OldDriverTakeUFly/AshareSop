"""End-to-end integration tests for the full StockHot-CN pipeline.

Uses mock data and patched external dependencies (AI API, network, PIL
Image.save) so no real API calls or file writes occur.
"""

from unittest.mock import patch, MagicMock

import pytest

import stockhot.ai_analyzer as ai_analyzer
import stockhot.hotspot_discovery as hd
import stockhot.image_generator as img_gen
import stockhot.research_report as rr
from PIL import Image

TEST_DATE = "2026-05-09"


@pytest.fixture
def mock_market_data():
    return {
        "gainers": [
            {
                "code": "000001",
                "name": "平安银行",
                "price": 15.5,
                "change_pct": 5.23,
                "volume": 1000000,
                "amount": 15500000,
            },
            {
                "code": "000002",
                "name": "万科A",
                "price": 10.2,
                "change_pct": 3.15,
                "volume": 800000,
                "amount": 8160000,
            },
            {
                "code": "600036",
                "name": "招商银行",
                "price": 35.8,
                "change_pct": 2.88,
                "volume": 500000,
                "amount": 17900000,
            },
        ],
        "losers": [
            {
                "code": "300001",
                "name": "特锐德",
                "price": 20.1,
                "change_pct": -3.5,
                "volume": 300000,
                "amount": 6030000,
            },
        ],
        "sectors": [
            {
                "name": "银行",
                "change_pct": 3.2,
                "volume": 5000000,
                "amount": 80000000,
                "turnover_rate": 1.5,
                "company_count": 42,
                "leader_stock": "平安银行",
            },
            {
                "name": "半导体",
                "change_pct": 2.8,
                "volume": 3000000,
                "amount": 60000000,
                "turnover_rate": 2.1,
                "company_count": 65,
                "leader_stock": "中芯国际",
            },
        ],
        "fund_flows": [
            {
                "name": "银行",
                "net_inflow": 5.2,
                "inflow": 8.1,
                "outflow": 2.9,
                "change_pct": 3.2,
                "category": "industry",
                "source": "ths",
                "leader_stock": "平安银行",
                "leader_change_pct": 5.23,
                "board_change_pct": 3.1,
            },
            {
                "name": "AI芯片",
                "net_inflow": 3.8,
                "inflow": 6.5,
                "outflow": 2.7,
                "change_pct": 2.5,
                "category": "concept",
                "source": "ths",
                "leader_stock": "寒武纪",
                "leader_change_pct": 4.1,
                "board_change_pct": 2.3,
            },
        ],
    }


# ---------------------------------------------------------------------------
# Test 1: collect → analyze pipeline
# ---------------------------------------------------------------------------
def test_collect_to_analyze(mock_market_data):
    """Save mock market data to DB -> run analysis -> verify result saved."""
    saved_analysis = {}

    def fake_save_analysis(date, analysis_type, result):
        saved_analysis[(date, analysis_type)] = result

    with (
        patch.object(
            ai_analyzer, "get_daily_data", return_value={**mock_market_data, "date": TEST_DATE}
        ),
        patch.object(ai_analyzer, "save_analysis_result", side_effect=fake_save_analysis),
        patch.object(
            ai_analyzer,
            "call_ai",
            return_value="由于银行板块资金持续流入，带动市场整体走强。\n风险提示：部分题材股炒作风险较大，建议谨慎对待。",
        ),
    ):
        result = ai_analyzer.run_analysis(TEST_DATE)

    assert result["date"] == TEST_DATE
    assert result["status"] == "success"
    # Hotspots analysis must have been saved
    assert (TEST_DATE, "hotspots") in saved_analysis
    hotspots = saved_analysis[(TEST_DATE, "hotspots")]
    assert "reasons" in hotspots
    assert isinstance(hotspots["reasons"], list)
    assert len(hotspots["reasons"]) > 0
    # Report must have been saved
    assert (TEST_DATE, "report") in saved_analysis
    assert "text" in saved_analysis[(TEST_DATE, "report")]


# ---------------------------------------------------------------------------
# Test 2: analyze → hotspot discovery pipeline
# ---------------------------------------------------------------------------
def test_analyze_to_hotspot_discovery(mock_market_data):
    """Use mock market data -> run hotspot discovery -> verify themes found."""
    with (
        patch.object(hd, "collect_news_events", return_value=[]),
    ):
        market_data = {**mock_market_data, "date": TEST_DATE}
        result = hd.build_hotspot_discovery(market_data, target_date=TEST_DATE)

    assert "themes" in result
    assert isinstance(result["themes"], list)
    assert len(result["themes"]) > 0
    assert "lead_theme" in result
    assert result["lead_theme"] is not None
    assert "method" in result
    assert result["method"] == "sample+public-news-v2.5"
    # Each theme should have required fields
    theme = result["themes"][0]
    assert "name" in theme
    assert "confidence" in theme
    assert "source_mode" in theme
    assert "matched_sectors" in theme


# ---------------------------------------------------------------------------
# Test 3: hotspot → research report pipeline
# ---------------------------------------------------------------------------
def test_hotspot_to_research_report(mock_market_data):
    """Use mock hotspot result -> generate theme report -> verify markdown output."""
    with (
        patch.object(rr, "get_daily_data", return_value={**mock_market_data, "date": TEST_DATE}),
        patch.object(rr, "get_preferred_analysis_result", return_value=None),
        patch.object(rr, "save_analysis_result"),
        patch.object(rr, "get_reports_dir_for_date") as mock_dir,
        patch.object(rr, "get_curated_theme_evidence", return_value=None),
        patch.object(rr, "normalize_theme_with_evidence_pack", side_effect=lambda t, e: t),
    ):
        mock_path = MagicMock()
        mock_path.__truediv__ = lambda self, other: self
        mock_path.write_text = MagicMock()
        mock_dir.return_value = mock_path

        result = rr.run_research_report(TEST_DATE, theme="银行")

    assert result["date"] == TEST_DATE
    assert result["status"] == "success"
    assert result["theme"] == "银行"


# ---------------------------------------------------------------------------
# Test 4: full pipeline (collect → analyze → hotspot → images)
# ---------------------------------------------------------------------------
def test_full_pipeline(mock_market_data):
    """Full flow: save data -> analyze -> hotspot discovery -> generate images."""
    saved_images = []

    def fake_save_image(date, image_type, file_path):
        saved_images.append({"date": date, "type": image_type, "path": file_path})

    # Pre-built hotspot discovery payload for the image generator to consume
    with (
        patch.object(hd, "collect_news_events", return_value=[]),
    ):
        market_data = {**mock_market_data, "date": TEST_DATE}
        hotspot_payload = hd.build_hotspot_discovery(market_data, target_date=TEST_DATE)

    # Mock analysis results
    hotspot_analysis = ai_analyzer.analyze_hotspots({**mock_market_data, "date": TEST_DATE})
    report_text = ai_analyzer.generate_daily_report(
        {**mock_market_data, "date": TEST_DATE}, hotspot_analysis
    )

    def fake_get_analysis(date, analysis_type):
        if analysis_type == "report":
            return {"text": report_text}
        if analysis_type == "hotspots":
            return hotspot_analysis
        if analysis_type == "hot_theme_report":
            return None
        return None

    def fake_get_preferred(date, types):
        for t in types:
            r = fake_get_analysis(date, t)
            if r:
                return r
        return None

    with (
        patch.object(img_gen, "save_image_path", side_effect=fake_save_image),
        patch.object(Image.Image, "save"),
    ):
        result = img_gen.run_generation(TEST_DATE)

    assert result["date"] == TEST_DATE
    assert result["status"] == "success"
    assert isinstance(result["images"], list)
    assert len(result["images"]) > 0
    # Verify image types were registered
    saved_types = {item["type"] for item in saved_images}
    assert "cover" in saved_types
    assert "gainers" in saved_types


# ---------------------------------------------------------------------------
# Test 5: pipeline with no data — graceful degradation
# ---------------------------------------------------------------------------
def test_pipeline_with_no_data():
    """Empty market data -> run analysis -> verify graceful degradation."""
    with (
        patch.object(ai_analyzer, "get_daily_data", return_value={"date": TEST_DATE}),
    ):
        result = ai_analyzer.run_analysis(TEST_DATE)

    assert result["status"] == "no_data"
    assert result["date"] == TEST_DATE


# ---------------------------------------------------------------------------
# Test 6: pipeline with partial data
# ---------------------------------------------------------------------------
def test_pipeline_with_partial_data():
    """Only sectors data, no gainers/fund_flows -> verify partial analysis works."""
    partial_data = {
        "date": TEST_DATE,
        "sectors": [
            {
                "name": "银行",
                "change_pct": 3.2,
                "volume": 5000000,
                "amount": 80000000,
                "turnover_rate": 1.5,
                "company_count": 42,
                "leader_stock": "平安银行",
            },
        ],
    }
    saved_analysis = {}

    def fake_save_analysis(date, analysis_type, result):
        saved_analysis[(date, analysis_type)] = result

    with (
        patch.object(ai_analyzer, "get_daily_data", return_value=partial_data),
        patch.object(ai_analyzer, "save_analysis_result", side_effect=fake_save_analysis),
        patch.object(
            ai_analyzer,
            "call_ai",
            return_value="由于银行板块资金持续流入，带动市场整体走强。\n风险提示：部分题材股炒作风险较大，建议谨慎对待。",
        ),
    ):
        result = ai_analyzer.run_analysis(TEST_DATE)

    assert result["status"] == "success"
    assert (TEST_DATE, "hotspots") in saved_analysis
    hotspots = saved_analysis[(TEST_DATE, "hotspots")]
    assert "risk_warnings" in hotspots
    assert len(hotspots["risk_warnings"]) > 0  # partial data should trigger warnings
