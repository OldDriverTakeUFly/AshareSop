"""Core configuration and constants for StockHot-CN."""

from pathlib import Path

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
STORAGE_DIR = PROJECT_ROOT / "stockhot" / "storage"
DB_PATH = STORAGE_DIR / "database" / "stockhot.db"
IMAGES_DIR = STORAGE_DIR / "files" / "images"
REPORTS_DIR = STORAGE_DIR / "files" / "reports"

# Ensure directories exist
DATA_DIR.mkdir(exist_ok=True)
STORAGE_DIR.mkdir(exist_ok=True)
IMAGES_DIR.mkdir(exist_ok=True)
REPORTS_DIR.mkdir(exist_ok=True)

# XiaoHongShu (Xiaohongshu) image dimensions
COVER_WIDTH = 1242
COVER_HEIGHT = 1660
CONTENT_WIDTH = 1240

# Market data config
TOP_N_STOCKS = 20
TOP_N_SECTORS = 15
TOP_N_FUNDS = 10

# Data retention (days)
DATA_RETENTION_DAYS = 90

# Colors for data visualization (Dark theme for Xiaohongshu)
COLOR_BACKGROUND = "#1a1a2e"
COLOR_CARD_BACKGROUND = "#16213e"
COLOR_PRIMARY = "#00d4ff"
COLOR_SUCCESS = "#00c853"
COLOR_DANGER = "#ff6b6b"
COLOR_WARNING = "#ffc107"
COLOR_TEXT_PRIMARY = "#ffffff"
COLOR_TEXT_SECONDARY = "#b0b0b0"
COLOR_BORDER = "#2d3a5a"