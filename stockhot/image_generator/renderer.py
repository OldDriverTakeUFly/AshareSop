"""Image rendering utilities for StockHot-CN."""

from PIL import Image, ImageDraw, ImageFont
from typing import Any

from stockhot.core.config import (
    COVER_WIDTH,
    COVER_HEIGHT,
    CONTENT_WIDTH,
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


def create_background(width: int, height: int) -> Image.Image:
    """Create a dark themed background."""
    img = Image.new("RGB", (width, height), COLOR_BACKGROUND)
    return img


def draw_card(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    width: int,
    height: int,
    title: str | None = None,
) -> None:
    """Draw a card with rounded corners and optional title."""
    draw.rounded_rectangle(
        [(x, y), (x + width, y + height)],
        radius=16,
        fill=COLOR_CARD_BACKGROUND,
        outline=COLOR_BORDER,
        width=1,
    )
    if title:
        draw.text((x + 20, y + 20), title, font=ImageFont.load_default(), fill=COLOR_PRIMARY)


def draw_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: int,
    y: int,
    font_size: int = 20,
    color: tuple[int, int, int] = COLOR_TEXT_PRIMARY,
    max_width: int | None = None,
) -> int:
    """Draw text and return the height used."""
    font = ImageFont.load_default()
    draw.text((x, y), text, font=font, fill=color)
    return font_size + 10


def get_change_color(pct: float) -> tuple[int, int, int]:
    """Get color based on percentage change."""
    if pct > 0:
        return COLOR_SUCCESS
    elif pct < 0:
        return COLOR_DANGER
    return COLOR_TEXT_SECONDARY


def format_number(num: float) -> str:
    """Format large numbers with unit suffix."""
    if abs(num) >= 1_0000_0000:
        return f"{num / 1_0000_0000:.2f}亿"
    elif abs(num) >= 1_0000:
        return f"{num / 1_0000:.2f}万"
    return f"{num:.2f}"