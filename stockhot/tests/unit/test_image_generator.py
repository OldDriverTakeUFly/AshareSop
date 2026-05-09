from pathlib import Path
from typing import cast

from PIL import Image, ImageDraw

import stockhot.image_generator as ig
from stockhot.image_generator import renderer


def test_looks_like_report_title_handles_common_variants():
    assert ig._looks_like_report_title("📊 市场复盘摘要") is True
    assert ig._looks_like_report_title("A股每日复盘：") is True
    assert ig._looks_like_report_title("样本概况：上涨样本20只") is False


def test_build_cover_narrative_uses_grounded_sample_language():
    market_data = {
        "gainers": [{"name": "N尚水", "change_pct": 286.72}],
        "sectors": [{"name": "电子设备", "change_pct": 4.96}],
        "fund_flows": [
            {"name": "通信设备", "net_inflow": 92.2, "source": "ths", "category": "industry"}
        ],
    }
    report = {"text": "## 市场复盘摘要\n样本概况：上涨样本20只，下跌样本20只，板块样本15个。"}
    hotspots = {"hotspots": []}

    narrative = ig._build_cover_narrative(market_data, report, hotspots)

    assert narrative[0] == ("今日市场", "样本概况：上涨样本20只，下跌样本20只，板块样本15个。")
    assert narrative[1][0] == "盘面线索"
    assert "热点线索仍偏分散" in narrative[1][1]
    assert narrative[2] == ("资金流向", "THS行业资金样本中，通信设备净流入约92.2亿")
    assert narrative[3] == ("后续观察", "下一交易日继续跟踪今日领先样本是否延续。")


def test_build_cover_narrative_prefers_hotspot_discovery_theme_labels():
    market_data = {
        "gainers": [{"name": "N尚水", "change_pct": 286.72}],
        "sectors": [{"name": "电子设备", "change_pct": 4.96}],
        "fund_flows": [
            {"name": "通信设备", "net_inflow": 92.2, "source": "ths", "category": "industry"}
        ],
    }
    report = {"text": "## 市场复盘摘要\n样本概况：上涨样本20只，下跌样本20只，板块样本15个。"}
    hotspots = {"lead_theme": "商业航天", "themes": [{"name": "商业航天"}, {"name": "卫星互联网"}]}

    narrative = ig._build_cover_narrative(market_data, report, hotspots)

    assert "商业航天、卫星互联网" in narrative[1][1]


def test_build_sector_rows_converts_amount_to_yi_and_formats_counts():
    market_data = {
        "sectors": [
            {
                "name": "电子设备",
                "change_pct": 4.96,
                "company_count": 48,
                "amount": 52916702080.0,
                "leader_stock": "创达新材",
            }
        ]
    }

    rows = ig._build_sector_rows(market_data)

    assert rows == [
        {
            "name": "电子设备",
            "today": "+4.96%",
            "company_count": "48",
            "amount": "529.2",
            "leader_stock": "创达新材",
            "color": ig.COLOR_UP,
        }
    ]


def test_build_sector_commentary_uses_converted_amount_and_truthful_text():
    rows = [
        {
            "name": "电子设备",
            "today": "+4.96%",
            "company_count": "48",
            "amount": "529.2",
            "leader_stock": "创达新材",
            "color": ig.COLOR_UP,
        }
    ]

    commentary = ig._build_sector_commentary(rows, {"hotspots": []})

    assert commentary[0] == "领先板块为电子设备，今日涨幅+4.96%，样本公司数48家。"
    assert commentary[1] == "当前数据源显示该板块领涨股为创达新材，成交额约529.2亿。"
    assert commentary[2] == "当前未识别到可校验热点主题。"
    assert commentary[3] == "本卡仅展示当前可核验字段：今日涨幅、公司数、成交额与领涨股。"


def test_build_sector_commentary_prefers_hotspot_discovery_labels():
    rows = [
        {
            "name": "电子设备",
            "today": "+4.96%",
            "company_count": "48",
            "amount": "529.2",
            "leader_stock": "创达新材",
            "color": ig.COLOR_UP,
        }
    ]
    hotspots = {"lead_theme": "商业航天", "themes": [{"name": "商业航天"}, {"name": "卫星互联网"}]}

    commentary = ig._build_sector_commentary(rows, hotspots)

    assert commentary[2] == "已校验热点提及：商业航天、卫星互联网。"


def test_build_leaderboard_rows_formats_real_extended_stock_fields():
    market_data = {
        "gainers": [
            {
                "code": "300750",
                "name": "宁德时代",
                "price": 198.56,
                "change_pct": 8.23,
                "amount": 15234567890.0,
                "turnover_rate": 6.78,
                "total_market_value": 87543000.0,
                "circulating_market_value": 81235000.0,
            }
        ]
    }

    rows = ig._build_leaderboard_rows(market_data)

    assert rows == [
        {
            "code": "300750",
            "name": "宁德时代",
            "subtitle": "",
            "price": "198.56",
            "change_pct": "+8.23%",
            "amount": "152.3",
            "turnover_rate": "6.78%",
            "total_market_value": "8754.3",
            "circulating_market_value": "8123.5",
            "color": ig.COLOR_UP,
        }
    ]


def test_shape_leaderboard_stock_text_keeps_code_and_more_name_visible():
    layout = ig._get_leaderboard_layout(10)
    draw = ImageDraw.Draw(Image.new("RGB", (ig.CONTENT_WIDTH, 200)))
    code_font = ig.get_font(14)
    name_font = ig.get_font(18)
    row = {
        "code": "600519",
        "name": "贵州茅台酒股份有限公司超长样本名称",
    }

    shaped = ig._shape_leaderboard_stock_text(
        draw,
        row,
        code_font,
        name_font,
        layout["col_widths"]["stock"],
    )
    single_line = ig._fit_text(
        draw,
        f"{row['code']} {row['name']}",
        name_font,
        layout["col_widths"]["stock"],
    )
    single_line_name = single_line.removeprefix(f"{row['code']} ").rstrip("…")

    assert shaped["code_line"] == "600519"
    assert shaped["name_line"].startswith("贵州茅台")
    assert shaped["name_line"].endswith("…")
    assert shaped["code_y_offset"] < shaped["name_y_offset"]
    assert len(shaped["name_line"].rstrip("…")) > len(single_line_name)
    assert (
        draw.textbbox((0, 0), shaped["code_line"], font=code_font)[2]
        <= layout["col_widths"]["stock"]
    )
    assert (
        draw.textbbox((0, 0), shaped["name_line"], font=name_font)[2]
        <= layout["col_widths"]["stock"]
    )


def test_get_leaderboard_layout_caps_compact_board_at_ten_rows_without_footer_collision():
    layout = ig._get_leaderboard_layout(18)

    assert layout["visible_row_count"] == 10
    assert layout["render_row_count"] == 10
    assert layout["table_bottom"] == layout["header_bottom"] + 10 * layout["row_height"]
    assert layout["footer_top"] == layout["table_bottom"] + layout["footer_gap"]
    assert layout["footer_bottom"] == (
        layout["footer_top"] + layout["footer_line_height"] * layout["footer_line_count"]
    )
    assert layout["image_height"] == layout["footer_bottom"] + layout["footer_padding_bottom"]


def test_generate_leaderboard_card_uses_red_white_board_style(tmp_path, monkeypatch):
    saved = []
    monkeypatch.setattr(ig, "get_images_dir_for_date", lambda date: tmp_path)
    monkeypatch.setattr(
        ig,
        "save_image_path",
        lambda date, image_type, file_path: saved.append((date, image_type, file_path)),
    )

    path = ig.generate_leaderboard_card({"date": "2026-04-17", "market_data": {"gainers": []}})
    layout = ig._get_leaderboard_layout(0)
    separator_x = layout["col_positions"]["price"] - 12

    with Image.open(path) as img:
        title_band_pixel = cast(tuple[int, int, int], img.getpixel((30, 40)))
        subheader_pixel = cast(tuple[int, int, int], img.getpixel((30, 90)))
        table_body_pixel = cast(
            tuple[int, int, int],
            img.getpixel((layout["board_right"] - 30, layout["header_bottom"] + 24)),
        )
        separator_pixel = cast(
            tuple[int, int, int], img.getpixel((separator_x, layout["header_bottom"] + 24))
        )

    assert title_band_pixel[0] > 170
    assert title_band_pixel[0] > title_band_pixel[1] + 120
    assert title_band_pixel[0] > title_band_pixel[2] + 120
    assert min(subheader_pixel) > 220
    assert min(table_body_pixel) > 240
    assert separator_pixel[0] > separator_pixel[1] > separator_pixel[2]
    assert saved == [("2026-04-17", "leaderboard", str(tmp_path / "leaderboard_2026-04-17.png"))]


def test_run_generation_handles_none_market_data(monkeypatch):
    saved = []
    monkeypatch.setattr(ig, "get_daily_data", lambda date: None)
    monkeypatch.setattr(ig, "get_analysis_result", lambda date, kind: None)
    monkeypatch.setattr(ig, "get_preferred_analysis_result", lambda date, kinds: None)
    monkeypatch.setattr(
        ig, "generate_cover", lambda data: saved.append(("cover", data)) or "/tmp/cover.png"
    )
    monkeypatch.setattr(
        ig,
        "generate_data_card",
        lambda data: saved.append((data["type"], data)) or f"/tmp/{data['type']}.png",
    )
    monkeypatch.setattr(
        ig,
        "generate_leaderboard_card",
        lambda data: saved.append(("leaderboard", data)) or "/tmp/leaderboard.png",
    )
    monkeypatch.setattr(
        ig,
        "generate_ths_fund_flow_leaderboard_card",
        lambda data: (
            saved.append(("ths_fund_flow_leaderboard", data))
            or "/tmp/ths_fund_flow_leaderboard.png"
        ),
    )
    monkeypatch.setattr(
        ig,
        "generate_theme_report_cards",
        lambda data: [],
    )
    monkeypatch.setattr(
        ig,
        "generate_hotspot_leaderboard_card",
        lambda data: saved.append(("hotspot_leaderboard", data)) or "/tmp/hotspot_leaderboard.png",
    )
    monkeypatch.setattr(
        ig,
        "generate_sector_card",
        lambda data: saved.append(("sectors_tracking", data)) or "/tmp/sectors_tracking.png",
    )

    result = ig.run_generation("2026-04-17")

    assert result == {
        "date": "2026-04-17",
        "status": "success",
        "images": [
            "/tmp/cover.png",
            "/tmp/hotspot_leaderboard.png",
            "/tmp/gainers.png",
            "/tmp/leaderboard.png",
            "/tmp/ths_fund_flow_leaderboard.png",
            "/tmp/sectors.png",
            "/tmp/sectors_tracking.png",
        ],
    }
    assert saved[0][1]["market_data"] == {}
    assert saved[1][0] == "gainers"
    assert saved[1][1]["market_data"] == {}
    assert saved[2][0] == "leaderboard"
    assert saved[2][1]["market_data"] == {}
    assert saved[3][0] == "ths_fund_flow_leaderboard"
    assert saved[3][1]["market_data"] == {}
    assert saved[4][0] == "hotspot_leaderboard"
    assert saved[4][1]["hotspots"] is None
    assert saved[5][0] == "sectors"
    assert saved[5][1]["market_data"] == {}
    assert saved[6][0] == "sectors_tracking"
    assert saved[6][1]["market_data"] == {}


def test_run_generation_renders_real_images_when_market_data_is_none(monkeypatch, tmp_path):
    saved = []
    monkeypatch.setattr(ig, "get_daily_data", lambda date: None)
    monkeypatch.setattr(ig, "get_analysis_result", lambda date, kind: None)
    monkeypatch.setattr(ig, "get_preferred_analysis_result", lambda date, kinds: None)
    monkeypatch.setattr(ig, "get_images_dir_for_date", lambda date: tmp_path)
    monkeypatch.setattr(
        ig,
        "save_image_path",
        lambda date, image_type, file_path: saved.append((date, image_type, file_path)),
    )

    result = ig.run_generation("2026-04-17")

    assert result["date"] == "2026-04-17"
    assert result["status"] == "success"
    assert len(result["images"]) == 7
    for path in result["images"]:
        assert tmp_path.joinpath(path.split("/")[-1]).exists()
    assert saved == [
        ("2026-04-17", "cover", str(tmp_path / "cover_2026-04-17.png")),
        ("2026-04-17", "gainers", str(tmp_path / "gainers_2026-04-17.png")),
        ("2026-04-17", "leaderboard", str(tmp_path / "leaderboard_2026-04-17.png")),
        (
            "2026-04-17",
            "ths_fund_flow_leaderboard",
            str(tmp_path / "ths_fund_flow_leaderboard_2026-04-17.png"),
        ),
        (
            "2026-04-17",
            "hotspot_leaderboard",
            str(tmp_path / "hotspot_leaderboard_2026-04-17.png"),
        ),
        ("2026-04-17", "sectors", str(tmp_path / "sectors_2026-04-17.png")),
        ("2026-04-17", "sectors_tracking", str(tmp_path / "sectors_tracking_2026-04-17.png")),
    ]


def test_renderer_helpers_keep_expected_formatting():
    assert renderer.get_change_color(1.0) == ig.COLOR_SUCCESS
    assert renderer.get_change_color(-1.0) == ig.COLOR_DANGER
    assert renderer.format_number(123456789.0) == "1.23亿"
    assert renderer.format_number(12345.0) == "1.23万"


def test_build_ths_fund_flow_rows_sorts_by_net_inflow_and_preserves_missingness():
    market_data = {
        "fund_flows": [
            {
                "source": "ths",
                "category": "industry",
                "name": "军工装备",
                "board_change_pct": 2.63,
                "net_inflow": 54.29,
                "inflow": 270.84,
                "outflow": 216.55,
                "leader_stock": "广联航空",
                "leader_change_pct": 11.53,
            },
            {
                "source": "ths",
                "category": "concept",
                "name": "卫星互联网",
                "board_change_pct": None,
                "net_inflow": None,
                "inflow": None,
                "outflow": None,
                "leader_stock": "-",
                "leader_change_pct": None,
            },
            {
                "source": "ths",
                "category": "industry",
                "name": "电网设备",
                "board_change_pct": 1.11,
                "net_inflow": 12.0,
                "inflow": 100.0,
                "outflow": 88.0,
                "leader_stock": "通光线缆",
                "leader_change_pct": -2.0,
            },
        ]
    }

    rows = ig._build_ths_fund_flow_rows(market_data)

    assert [row["name"] for row in rows] == ["军工装备", "电网设备", "卫星互联网"]
    assert rows[2]["net_inflow"] == "-"
    assert rows[2]["inflow"] == "-"
    assert rows[2]["outflow"] == "-"
    assert rows[2]["board_change_pct"] == "-"
    assert rows[2]["leader_change_pct"] == "-"
    assert rows[2]["net_color"] == ig.COLOR_TEXT_SECONDARY
    assert rows[2]["board_change_color"] == ig.COLOR_TEXT_SECONDARY
    assert rows[2]["leader_change_color"] == ig.COLOR_TEXT_SECONDARY


def test_generate_ths_fund_flow_leaderboard_card_saves_image(monkeypatch, tmp_path):
    saved = []
    monkeypatch.setattr(ig, "get_images_dir_for_date", lambda date: tmp_path)
    monkeypatch.setattr(
        ig,
        "save_image_path",
        lambda date, image_type, file_path: saved.append((date, image_type, file_path)),
    )

    market_data = {
        "fund_flows": [
            {
                "source": "ths",
                "category": "industry",
                "name": "军工装备",
                "board_change_pct": 2.63,
                "net_inflow": 54.29,
                "inflow": 270.84,
                "outflow": 216.55,
                "leader_stock": "广联航空",
                "leader_change_pct": 11.53,
            }
        ]
    }

    path = ig.generate_ths_fund_flow_leaderboard_card(
        {"date": "2026-04-17", "market_data": market_data}
    )

    assert path == str(tmp_path / "ths_fund_flow_leaderboard_2026-04-17.png")
    assert (tmp_path / "ths_fund_flow_leaderboard_2026-04-17.png").exists()
    assert saved == [
        (
            "2026-04-17",
            "ths_fund_flow_leaderboard",
            str(tmp_path / "ths_fund_flow_leaderboard_2026-04-17.png"),
        )
    ]


def test_generate_ths_fund_flow_leaderboard_card_handles_empty_state(monkeypatch, tmp_path):
    saved = []
    monkeypatch.setattr(ig, "get_images_dir_for_date", lambda date: tmp_path)
    monkeypatch.setattr(
        ig,
        "save_image_path",
        lambda date, image_type, file_path: saved.append((date, image_type, file_path)),
    )

    path = ig.generate_ths_fund_flow_leaderboard_card({"date": "2026-04-17", "market_data": {}})

    assert path == str(tmp_path / "ths_fund_flow_leaderboard_2026-04-17.png")
    assert (tmp_path / "ths_fund_flow_leaderboard_2026-04-17.png").exists()
    assert saved[0][1] == "ths_fund_flow_leaderboard"


def test_build_hotspot_leaderboard_rows_formats_theme_candidates():
    hotspots = {
        "themes": [
            {
                "name": "商业航天",
                "confidence": "high",
                "confidence_score": 1.05,
                "source_mode": "sample+evidence",
                "matched_sectors": [
                    {"name": "商业航天", "change_pct": 4.96, "leader_stock": "创达新材"}
                ],
                "matched_fund_flows": [
                    {
                        "name": "商业航天",
                        "net_inflow": 92.22,
                        "scope": "THS行业",
                        "leader_stock": "创达新材",
                    }
                ],
                "matched_stocks": [],
                "news_signals": [{"title": "力箭二号遥一首飞成功"}],
                "evidence_sources": [{"tier": "一级证据", "items": ["国家航天局：行动计划"]}],
                "summary": "公开资料中，力箭二号遥一首飞成功。 板块样本中，商业航天涨幅+4.96%。",
            }
        ]
    }

    rows = ig._build_hotspot_leaderboard_rows(hotspots)

    assert rows == [
        {
            "theme": "商业航天",
            "confidence": "high (1.05)",
            "source_mode": "sample+evidence",
            "signals": "板1 资1 股0 闻1",
            "summary": "公开资料中，力箭二号遥一首飞成功。 板块样本中，商业航天涨幅+4.96%。",
        }
    ]


def test_generate_hotspot_leaderboard_card_saves_image(monkeypatch, tmp_path):
    saved = []
    monkeypatch.setattr(ig, "get_images_dir_for_date", lambda date: tmp_path)
    monkeypatch.setattr(
        ig,
        "save_image_path",
        lambda date, image_type, file_path: saved.append((date, image_type, file_path)),
    )

    hotspots = {
        "themes": [
            {
                "name": "商业航天",
                "confidence": "high",
                "confidence_score": 1.05,
                "source_mode": "sample+evidence",
                "matched_sectors": [
                    {"name": "商业航天", "change_pct": 4.96, "leader_stock": "创达新材"}
                ],
                "matched_fund_flows": [
                    {
                        "name": "商业航天",
                        "net_inflow": 92.22,
                        "scope": "THS行业",
                        "leader_stock": "创达新材",
                    }
                ],
                "matched_stocks": [],
                "news_signals": [{"title": "力箭二号遥一首飞成功"}],
                "evidence_sources": [{"tier": "一级证据", "items": ["国家航天局：行动计划"]}],
                "summary": "公开资料中，力箭二号遥一首飞成功。",
            }
        ]
    }

    path = ig.generate_hotspot_leaderboard_card({"date": "2026-04-17", "hotspots": hotspots})

    assert path == str(tmp_path / "hotspot_leaderboard_2026-04-17.png")
    assert (tmp_path / "hotspot_leaderboard_2026-04-17.png").exists()
    assert saved == [
        (
            "2026-04-17",
            "hotspot_leaderboard",
            str(tmp_path / "hotspot_leaderboard_2026-04-17.png"),
        )
    ]


def test_generate_hotspot_leaderboard_card_handles_empty_state(monkeypatch, tmp_path):
    saved = []
    monkeypatch.setattr(ig, "get_images_dir_for_date", lambda date: tmp_path)
    monkeypatch.setattr(
        ig,
        "save_image_path",
        lambda date, image_type, file_path: saved.append((date, image_type, file_path)),
    )

    path = ig.generate_hotspot_leaderboard_card({"date": "2026-04-17", "hotspots": {}})

    assert path == str(tmp_path / "hotspot_leaderboard_2026-04-17.png")
    assert (tmp_path / "hotspot_leaderboard_2026-04-17.png").exists()
    assert saved[0][1] == "hotspot_leaderboard"


def test_generate_theme_report_cards_saves_four_cards(monkeypatch, tmp_path):
    saved = []
    monkeypatch.setattr(ig, "get_images_dir_for_date", lambda date: tmp_path)
    monkeypatch.setattr(
        ig,
        "save_image_path",
        lambda date, image_type, file_path: saved.append((date, image_type, file_path)),
    )

    report = {
        "theme": "商业航天",
        "catalyst": "近期商业航天的公开催化，主要集中在可重复使用火箭验证、政策支持和发射基础设施建设。",
        "core_judgment": "围绕“商业航天”的公开资料催化已逐步增多。",
        "current_status": ["2026-03-30｜新华社/新华网：力箭二号遥一首飞成功。"],
        "chain_segments": ["运载火箭总装与回收验证"],
        "next_milestones": ["继续跟踪可重复使用火箭后续回收验证是否从试验段走向稳定复用。"],
        "targets": [{"code": "688333", "name": "铂力特", "reason": "公开资料线索：航天部件配套。"}],
    }

    paths = ig.generate_theme_report_cards({"date": "2026-04-17", "report": report})

    assert len(paths) == 4
    assert [Path(path).name for path in paths] == [
        "theme_report_cover_2026-04-17.png",
        "theme_report_evidence_2026-04-17.png",
        "theme_report_milestones_2026-04-17.png",
        "theme_report_targets_2026-04-17.png",
    ]
    assert [item[1] for item in saved] == [
        "theme_report_cover",
        "theme_report_evidence",
        "theme_report_milestones",
        "theme_report_targets",
    ]
    for path in paths:
        assert tmp_path.joinpath(Path(path).name).exists()


def test_run_generation_includes_theme_report_cards_when_report_exists(monkeypatch):
    monkeypatch.setattr(ig, "get_daily_data", lambda date: {})
    monkeypatch.setattr(
        ig,
        "get_analysis_result",
        lambda date, kind: {
            "hot_theme_report": {
                "theme": "商业航天",
                "catalyst": "催化",
                "core_judgment": "判断",
                "current_status": [],
                "chain_segments": [],
                "next_milestones": [],
                "targets": [],
            }
        }.get(kind),
    )
    monkeypatch.setattr(ig, "generate_cover", lambda data: "/tmp/cover.png")
    monkeypatch.setattr(ig, "generate_data_card", lambda data: f"/tmp/{data['type']}.png")
    monkeypatch.setattr(ig, "generate_leaderboard_card", lambda data: "/tmp/leaderboard.png")
    monkeypatch.setattr(
        ig,
        "generate_ths_fund_flow_leaderboard_card",
        lambda data: "/tmp/ths_fund_flow_leaderboard.png",
    )
    monkeypatch.setattr(ig, "generate_sector_card", lambda data: "/tmp/sectors_tracking.png")
    monkeypatch.setattr(
        ig,
        "generate_theme_report_cards",
        lambda data: [
            "/tmp/theme_report_cover.png",
            "/tmp/theme_report_evidence.png",
            "/tmp/theme_report_milestones.png",
            "/tmp/theme_report_targets.png",
        ],
    )
    monkeypatch.setattr(
        ig, "generate_hotspot_leaderboard_card", lambda data: "/tmp/hotspot_leaderboard.png"
    )

    result = ig.run_generation("2026-04-17")

    assert result["images"] == [
        "/tmp/cover.png",
        "/tmp/theme_report_cover.png",
        "/tmp/theme_report_evidence.png",
        "/tmp/theme_report_milestones.png",
        "/tmp/theme_report_targets.png",
        "/tmp/hotspot_leaderboard.png",
        "/tmp/gainers.png",
        "/tmp/leaderboard.png",
        "/tmp/ths_fund_flow_leaderboard.png",
        "/tmp/sectors.png",
        "/tmp/sectors_tracking.png",
    ]
