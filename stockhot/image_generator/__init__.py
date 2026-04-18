"""Image generation module for StockHot-CN."""

import os
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
    """Generate cover image for Xiaohongshu."""
    img = create_background(COVER_WIDTH, COVER_HEIGHT)
    draw = ImageDraw.Draw(img)

    title_text = f"📈 A股每日热点分析"
    date_text = data.get("date", datetime.now().strftime("%Y-%m-%d"))

    font_title = ImageFont.load_default()
    font_subtitle = ImageFont.load_default()

    draw.text((COVER_WIDTH // 2 - 100, 200), title_text, font=font_title, fill=COLOR_PRIMARY)
    draw.text((COVER_WIDTH // 2 - 80, 260), date_text, font=font_subtitle, fill=COLOR_TEXT_SECONDARY)

    draw.text((100, 400), "今日市场亮点:", font=font_subtitle, fill=COLOR_TEXT_PRIMARY)
    draw.text((100, 450), "• 银行板块持续走强", font=font_subtitle, fill=COLOR_SUCCESS)
    draw.text((100, 500), "• 白酒股超跌反弹", font=font_subtitle, fill=COLOR_SUCCESS)
    draw.text((100, 550), "• 资金大幅流入蓝筹", font=font_subtitle, fill=COLOR_PRIMARY)

    filename = f"cover_{data.get('date', 'today')}.png"
    filepath = IMAGES_DIR / filename
    img.save(filepath)

    save_image_path(data.get("date", datetime.now().strftime("%Y-%m-%d")), "cover", str(filepath))

    return str(filepath)


def generate_data_card(data: dict) -> str:
    """Generate data card image (gainers/losers/sectors)."""
    card_type = data.get("type", "gainers")
    date = data.get("date", datetime.now().strftime("%Y-%m-%d"))

    img = create_background(CONTENT_WIDTH, 1200)
    draw = ImageDraw.Draw(img)

    titles = {
        "gainers": "📈 今日涨幅榜 TOP10",
        "losers": "📉 今日跌幅榜 TOP10",
        "sectors": "🔥 热门板块排行",
    }

    draw.text((40, 40), titles.get(card_type, "数据卡片"), font=ImageFont.load_default(), fill=COLOR_PRIMARY)

    sample_data = [
        {"name": "平安银行", "value": "+10.02%", "color": COLOR_SUCCESS},
        {"name": "贵州茅台", "value": "+8.32%", "color": COLOR_SUCCESS},
        {"name": "万科A", "value": "+7.85%", "color": COLOR_SUCCESS},
        {"name": "招商银行", "value": "+6.54%", "color": COLOR_SUCCESS},
        {"name": "中国平安", "value": "+5.92%", "color": COLOR_SUCCESS},
    ]

    y_offset = 120
    for item in sample_data:
        draw.text((60, y_offset), f"  {item['name']}", font=ImageFont.load_default(), fill=COLOR_TEXT_PRIMARY)
        draw.text((CONTENT_WIDTH - 150, y_offset), item['value'], font=ImageFont.load_default(), fill=item['color'])
        y_offset += 80

    filename = f"{card_type}_{date}.png"
    filepath = IMAGES_DIR / filename
    img.save(filepath)

    save_image_path(date, card_type, str(filepath))

    return str(filepath)


def generate_report_image(report: str) -> str:
    """Generate full report image."""
    date = datetime.now().strftime("%Y-%m-%d")

    img = create_background(CONTENT_WIDTH, 2000)
    draw = ImageDraw.Draw(img)

    draw.text((40, 40), f"📊 每日市场报告 - {date}", font=ImageFont.load_default(), fill=COLOR_PRIMARY)

    y = 120
    for line in report.split("\n")[:30]:
        if line.strip():
            draw.text((40, y), line[:50], font=ImageFont.load_default(), fill=COLOR_TEXT_PRIMARY)
            y += 40
            if y > 1900:
                break

    filename = f"report_{date}.png"
    filepath = IMAGES_DIR / filename
    img.save(filepath)

    save_image_path(date, "report", str(filepath))

    return str(filepath)