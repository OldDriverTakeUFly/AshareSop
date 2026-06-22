"""Image generation module for StockHot-CN."""

import random
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

from stockhot.core.config import (
    COVER_WIDTH,
    COVER_HEIGHT,
    CONTENT_WIDTH,
    IMAGES_DIR,
    COLOR_BACKGROUND,
    COLOR_CARD_BACKGROUND,
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    COLOR_DANGER,
    COLOR_WARNING,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_SECONDARY,
    COLOR_BORDER,
    COLOR_UP,
    COLOR_DOWN,
)
from stockhot.data_collector.sector_map import get_stock_sector
from stockhot.image_generator.renderer import (
    create_background,
    draw_card,
    get_change_color,
    format_number,
)
from stockhot.storage.database import get_analysis_result, save_image_path

# Re-exports consumed by callers/tests via `from stockhot.image_generator import ...`.
# Declaring __all__ also silences ruff F401 for these intentional re-exports.
__all__ = [
    "COLOR_BACKGROUND",
    "COLOR_CARD_BACKGROUND",
    "COLOR_SUCCESS",
    "COLOR_DANGER",
    "create_background",
    "draw_card",
    "get_change_color",
    "format_number",
    "get_analysis_result",
]


def run_generation(date: str | None = None) -> dict:
    """Run image generation for specified date."""
    target_date = date or datetime.now().strftime("%Y-%m-%d")
    print(f"[ImageGenerator] 生成日期: {target_date}")

    cover_path = generate_cover({"date": target_date})
    gainers_path = generate_data_card({"type": "gainers", "date": target_date})
    sectors_path = generate_data_card({"type": "sectors", "date": target_date})
    sector_tracking_path = generate_sector_card({"date": target_date})

    print("[ImageGenerator] 生成完成")
    return {
        "date": target_date,
        "status": "success",
        "images": [cover_path, gainers_path, sectors_path, sector_tracking_path],
    }


def generate_cover(data: dict) -> str:
    """Generate cyberpunk styled cover image for Xiaohongshu."""
    img = create_cyberpunk_background(COVER_WIDTH, COVER_HEIGHT)
    draw = ImageDraw.Draw(img)

    date_str = data.get("date", datetime.now().strftime("%Y-%m-%d"))

    draw_cyberpunk_border(draw, COVER_WIDTH, COVER_HEIGHT)

    title = "A股每日复盘"
    date_formatted = f"{date_str[5:]}"

    draw.text((COVER_WIDTH // 2 - 120, 120), title, font=get_font(48), fill=COLOR_PRIMARY)

    draw.text(
        (COVER_WIDTH // 2 - 80, 190), date_formatted, font=get_font(28), fill=COLOR_TEXT_SECONDARY
    )

    draw.line([(100, 250), (COVER_WIDTH - 100, 250)], fill=COLOR_PRIMARY, width=2)

    narrative = [
        ("今日市场", "今天的市场演绎了一场"),
        ("银行板块", "银行板块全天强势，"),
        ("资金流向", "资金方面，主力资金"),
        ("明日展望", "明日关注"),
    ]

    content_y = 300
    for label, text in narrative:
        draw.text((100, content_y), f"▸ {label}", font=get_font(32), fill=COLOR_PRIMARY)
        content_y += 50
        draw.text((100, content_y), f"  {text}", font=get_font(26), fill=COLOR_TEXT_PRIMARY)
        content_y += 70

    draw_stats_section(draw, date_str)

    filename = f"cover_{data.get('date', 'today')}.png"
    filepath = IMAGES_DIR / filename
    img.save(filepath)

    save_image_path(data.get("date", datetime.now().strftime("%Y-%m-%d")), "cover", str(filepath))

    return str(filepath)


def create_cyberpunk_background(width: int, height: int) -> Image.Image:
    """Create a cyberpunk styled background with subtle effects."""
    img = Image.new("RGB", (width, height), (10, 10, 20))
    draw = ImageDraw.Draw(img)

    for x in range(0, width, 80):
        draw.line([(x, 0), (x, height)], fill=(30, 40, 60), width=1)

    for y in range(0, height, 80):
        draw.line([(0, y), (width, y)], fill=(30, 40, 60), width=1)

    for _ in range(20):
        x = random.randint(50, width - 50)
        y = random.randint(50, height - 50)
        size = random.randint(1, 3)
        draw.ellipse([x, y, x + size, y + size], fill=(0, 212, 255, 80))

    return img


def draw_cyberpunk_border(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    """Draw cyberpunk styled border with corner markers."""
    border_color = COLOR_PRIMARY

    draw.rectangle([(20, 20), (width - 20, height - 20)], outline=border_color, width=3)

    corner_size = 40
    draw.line([(20, 20 + corner_size), (20, 20)], fill=border_color, width=4)
    draw.line([(20, 20), (20 + corner_size, 20)], fill=border_color, width=4)

    draw.line([(width - 20 - corner_size, 20), (width - 20, 20)], fill=border_color, width=4)
    draw.line([(width - 20, 20), (width - 20, 20 + corner_size)], fill=border_color, width=4)

    draw.line([(20, height - 20 - corner_size), (20, height - 20)], fill=border_color, width=4)
    draw.line([(20, height - 20), (20 + corner_size, height - 20)], fill=border_color, width=4)

    draw.line(
        [(width - 20 - corner_size, height - 20), (width - 20, height - 20)],
        fill=border_color,
        width=4,
    )
    draw.line(
        [(width - 20, height - 20 - corner_size), (width - 20, height - 20)],
        fill=border_color,
        width=4,
    )


def draw_stats_section(draw: ImageDraw.ImageDraw, date_str: str) -> None:
    """Draw statistics section at bottom."""
    section_y = 750

    draw.line([(100, section_y), (COVER_WIDTH - 100, section_y)], fill=COLOR_BORDER, width=1)

    stats = [
        ("上涨", "2,345", COLOR_UP),
        ("下跌", "1,892", COLOR_DOWN),
        ("涨停", "47", COLOR_PRIMARY),
        ("成交额", "9,823亿", COLOR_WARNING),
    ]

    x_start = 120
    for label, value, color in stats:
        draw.text((x_start, section_y + 20), label, font=get_font(22), fill=COLOR_TEXT_SECONDARY)
        draw.text((x_start, section_y + 50), value, font=get_font(30), fill=color)
        x_start += 280


def get_font(size: int) -> ImageFont.FreeTypeFont:
    """Get font with specified size - try Chinese fonts first, fallback to default."""
    chinese_fonts = [
        "/usr/share/fonts/truetype/arphic/ukai.ttc",
        "/usr/share/fonts/truetype/arphic/uming.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for font_path in chinese_fonts:
        try:
            return ImageFont.truetype(font_path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def generate_data_card(data: dict) -> str:
    """Generate data card image (gainers/losers/sectors)."""
    card_type = data.get("type", "gainers")
    date = data.get("date", datetime.now().strftime("%Y-%m-%d"))

    img = create_cyberpunk_background(CONTENT_WIDTH, 1200)
    draw = ImageDraw.Draw(img)

    draw_cyberpunk_border(draw, CONTENT_WIDTH, 1200)

    titles = {
        "gainers": "📈 今日涨幅榜 TOP10",
        "losers": "📉 今日跌幅榜 TOP10",
        "sectors": "🔥 热门板块排行",
    }

    draw.text((50, 50), titles.get(card_type, "数据卡片"), font=get_font(36), fill=COLOR_PRIMARY)

    sample_data = [
        {"name": "平安银行", "value": "+10.02%", "color": COLOR_UP},
        {"name": "贵州茅台", "value": "+8.32%", "color": COLOR_UP},
        {"name": "万科A", "value": "+7.85%", "color": COLOR_UP},
        {"name": "招商银行", "value": "+6.54%", "color": COLOR_UP},
        {"name": "中国平安", "value": "+5.92%", "color": COLOR_UP},
    ]

    y_offset = 140
    for i, item in enumerate(sample_data):
        sector = get_stock_sector(item["name"])
        draw.rectangle([(50, y_offset), (70, y_offset + 40)], fill=item["color"])
        draw.text((90, y_offset + 5), f"#{i+1}", font=get_font(22), fill=COLOR_TEXT_SECONDARY)
        draw.text((140, y_offset + 5), item["name"], font=get_font(28), fill=COLOR_TEXT_PRIMARY)
        draw.text(
            (140, y_offset + 35), f"  [{sector}]", font=get_font(18), fill=COLOR_TEXT_SECONDARY
        )
        draw.text(
            (CONTENT_WIDTH - 160, y_offset + 5),
            item["value"],
            font=get_font(28),
            fill=item["color"],
        )
        y_offset += 100

    filename = f"{card_type}_{date}.png"
    filepath = IMAGES_DIR / filename
    img.save(filepath)

    save_image_path(date, card_type, str(filepath))

    return str(filepath)


def generate_sector_card(data: dict) -> str:
    """Generate sector performance card with multi-period tracking."""
    date = data.get("date", datetime.now().strftime("%Y-%m-%d"))

    img = create_cyberpunk_background(CONTENT_WIDTH, 1550)
    draw = ImageDraw.Draw(img)

    draw_cyberpunk_border(draw, CONTENT_WIDTH, 1550)

    draw.text((50, 50), "🔥 板块强度追踪", font=get_font(36), fill=COLOR_PRIMARY)

    col_positions = {
        "sector": 50,
        "today": 280,
        "3d": 420,
        "1m": 560,
        "fund_today": 700,
        "fund_5d": 880,
    }

    draw.text((col_positions["sector"], 100), "板块", font=get_font(20), fill=COLOR_TEXT_SECONDARY)
    draw.text((col_positions["today"], 100), "今日", font=get_font(20), fill=COLOR_TEXT_SECONDARY)
    draw.text((col_positions["3d"], 100), "近3日", font=get_font(20), fill=COLOR_TEXT_SECONDARY)
    draw.text((col_positions["1m"], 100), "近1月", font=get_font(20), fill=COLOR_TEXT_SECONDARY)
    draw.text(
        (col_positions["fund_today"], 100), "当日资金", font=get_font(20), fill=COLOR_TEXT_SECONDARY
    )
    draw.text(
        (col_positions["fund_5d"], 100), "5日资金", font=get_font(20), fill=COLOR_TEXT_SECONDARY
    )

    draw.line([(50, 130), (CONTENT_WIDTH - 50, 130)], fill=COLOR_BORDER, width=1)

    sample_sectors = [
        {
            "name": "银行",
            "today": "+3.45%",
            "3d": "+5.23%",
            "1m": "+8.12%",
            "fund_today": "+45.2亿",
            "fund_5d": "+128.5亿",
            "color": COLOR_UP,
        },
        {
            "name": "白酒",
            "today": "+2.18%",
            "3d": "+3.56%",
            "1m": "-2.34%",
            "fund_today": "+23.8亿",
            "fund_5d": "+56.2亿",
            "color": COLOR_UP,
        },
        {
            "name": "半导体",
            "today": "+1.87%",
            "3d": "-1.23%",
            "1m": "-5.67%",
            "fund_today": "-12.3亿",
            "fund_5d": "-35.6亿",
            "color": COLOR_UP,
        },
        {
            "name": "新能源车",
            "today": "+1.45%",
            "3d": "+2.89%",
            "1m": "+12.34%",
            "fund_today": "+18.5亿",
            "fund_5d": "+42.3亿",
            "color": COLOR_UP,
        },
        {
            "name": "房地产",
            "today": "-0.89%",
            "3d": "-2.45%",
            "1m": "-8.76%",
            "fund_today": "-8.9亿",
            "fund_5d": "-25.4亿",
            "color": COLOR_DOWN,
        },
        {
            "name": "医药",
            "today": "-1.23%",
            "3d": "-3.12%",
            "1m": "-10.23%",
            "fund_today": "-15.6亿",
            "fund_5d": "-48.2亿",
            "color": COLOR_DOWN,
        },
    ]

    y_offset = 160
    for i, sector in enumerate(sample_sectors):
        draw.rectangle([(50, y_offset + 5), (70, y_offset + 35)], fill=sector["color"])
        draw.text((90, y_offset + 5), f"#{i+1}", font=get_font(18), fill=COLOR_TEXT_SECONDARY)
        draw.text((140, y_offset + 8), sector["name"], font=get_font(24), fill=COLOR_TEXT_PRIMARY)

        draw.text(
            (col_positions["today"], y_offset + 8),
            sector["today"],
            font=get_font(22),
            fill=sector["color"],
        )
        color_3d = COLOR_UP if "+" in sector["3d"] else COLOR_DOWN
        draw.text(
            (col_positions["3d"], y_offset + 8), sector["3d"], font=get_font(22), fill=color_3d
        )
        color_1m = COLOR_UP if "+" in sector["1m"] else COLOR_DOWN
        draw.text(
            (col_positions["1m"], y_offset + 8), sector["1m"], font=get_font(22), fill=color_1m
        )

        fund_today_color = COLOR_UP if "+" in sector["fund_today"] else COLOR_DOWN
        draw.text(
            (col_positions["fund_today"], y_offset + 8),
            sector["fund_today"],
            font=get_font(22),
            fill=fund_today_color,
        )

        fund_5d_color = COLOR_UP if "+" in sector["fund_5d"] else COLOR_DOWN
        draw.text(
            (col_positions["fund_5d"], y_offset + 8),
            sector["fund_5d"],
            font=get_font(22),
            fill=fund_5d_color,
        )

        y_offset += 90

    draw.line([(50, 780), (CONTENT_WIDTH - 50, 780)], fill=COLOR_BORDER, width=1)
    draw.text((50, 800), "📊 趋势解读", font=get_font(24), fill=COLOR_PRIMARY)
    draw.text(
        (50, 840),
        "银行板块持续强势，资金大幅净流入，短期有望继续走强",
        font=get_font(18),
        fill=COLOR_TEXT_SECONDARY,
    )
    draw.text(
        (50, 870),
        "新能源车延续反弹，关注回调后的低吸机会",
        font=get_font(18),
        fill=COLOR_TEXT_SECONDARY,
    )
    draw.text(
        (50, 900),
        "医药板块资金持续流出，建议观望为主",
        font=get_font(18),
        fill=COLOR_TEXT_SECONDARY,
    )

    filename = f"sectors_tracking_{date}.png"
    filepath = IMAGES_DIR / filename
    img.save(filepath)

    save_image_path(date, "sectors_tracking", str(filepath))

    return str(filepath)
