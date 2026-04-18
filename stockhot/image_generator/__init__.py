"""Image generation module for StockHot-CN."""

import os
import random
from datetime import datetime
from pathlib import Path

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
)
from stockhot.image_generator.renderer import create_background, draw_card, get_change_color, format_number
from stockhot.storage.database import get_analysis_result, save_image_path


def run_generation(date: str | None = None) -> dict:
    """Run image generation for specified date."""
    target_date = date or datetime.now().strftime("%Y-%m-%d")
    print(f"[ImageGenerator] 生成日期: {target_date}")

    cover_path = generate_cover({"date": target_date})
    gainers_path = generate_data_card({"type": "gainers", "date": target_date})
    sectors_path = generate_data_card({"type": "sectors", "date": target_date})

    print("[ImageGenerator] 生成完成")
    return {
        "date": target_date,
        "status": "success",
        "images": [cover_path, gainers_path, sectors_path],
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

    draw.text((COVER_WIDTH // 2 - 80, 190), date_formatted, font=get_font(28), fill=COLOR_TEXT_SECONDARY)

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

    draw.line([(width - 20 - corner_size, height - 20), (width - 20, height - 20)], fill=border_color, width=4)
    draw.line([(width - 20, height - 20 - corner_size), (width - 20, height - 20)], fill=border_color, width=4)


def draw_stats_section(draw: ImageDraw.ImageDraw, date_str: str) -> None:
    """Draw statistics section at bottom."""
    section_y = 750

    draw.line([(100, section_y), (COVER_WIDTH - 100, section_y)], fill=COLOR_BORDER, width=1)

    stats = [
        ("上涨", "2,345", COLOR_SUCCESS),
        ("下跌", "1,892", COLOR_DANGER),
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
        except:
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
        {"name": "平安银行", "value": "+10.02%", "color": COLOR_SUCCESS},
        {"name": "贵州茅台", "value": "+8.32%", "color": COLOR_SUCCESS},
        {"name": "万科A", "value": "+7.85%", "color": COLOR_SUCCESS},
        {"name": "招商银行", "value": "+6.54%", "color": COLOR_SUCCESS},
        {"name": "中国平安", "value": "+5.92%", "color": COLOR_SUCCESS},
    ]

    y_offset = 140
    for item in sample_data:
        draw.rectangle([(50, y_offset), (100, y_offset + 40)], fill=item["color"])
        draw.text((120, y_offset + 5), item["name"], font=get_font(28), fill=COLOR_TEXT_PRIMARY)
        draw.text((CONTENT_WIDTH - 180, y_offset + 5), item["value"], font=get_font(28), fill=item["color"])
        y_offset += 90

    filename = f"{card_type}_{date}.png"
    filepath = IMAGES_DIR / filename
    img.save(filepath)

    save_image_path(date, card_type, str(filepath))

    return str(filepath)


def generate_report_image(report: str) -> str:
    """Generate full report image."""
    date = datetime.now().strftime("%Y-%m-%d")

    img = create_cyberpunk_background(CONTENT_WIDTH, 2000)
    draw = ImageDraw.Draw(img)

    draw_cyberpunk_border(draw, CONTENT_WIDTH, 2000)

    draw.text((50, 50), f"📊 每日市场报告 - {date}", font=get_font(36), fill=COLOR_PRIMARY)

    y = 140
    for line in report.split("\n")[:30]:
        if line.strip():
            draw.text((50, y), line[:50], font=get_font(24), fill=COLOR_TEXT_PRIMARY)
            y += 50
            if y > 1900:
                break

    filename = f"report_{date}.png"
    filepath = IMAGES_DIR / filename
    img.save(filepath)

    save_image_path(date, "report", str(filepath))

    return str(filepath)