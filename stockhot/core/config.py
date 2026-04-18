"""Core configuration and constants for StockHot-CN."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
STORAGE_DIR = PROJECT_ROOT / "storage"
DB_PATH = STORAGE_DIR / "database" / "stockhot.db"
IMAGES_DIR = STORAGE_DIR / "files" / "images"
REPORTS_DIR = STORAGE_DIR / "files" / "reports"

DATA_DIR.mkdir(exist_ok=True)
STORAGE_DIR.mkdir(exist_ok=True)
(STORAGE_DIR / "files").mkdir(exist_ok=True)
(STORAGE_DIR / "files" / "images").mkdir(exist_ok=True)
(STORAGE_DIR / "files" / "reports").mkdir(exist_ok=True)
(STORAGE_DIR / "database").mkdir(exist_ok=True)

COVER_WIDTH = 1242
COVER_HEIGHT = 1660
CONTENT_WIDTH = 1240

TOP_N_STOCKS = 20
TOP_N_SECTORS = 15
TOP_N_FUNDS = 10

DATA_RETENTION_DAYS = 90

SCHEDULER_WORKFLOW_HOUR = 16
SCHEDULER_WORKFLOW_MINUTE = 30
SCHEDULER_CLEANUP_HOUR = 3
SCHEDULER_CLEANUP_MINUTE = 0

COLOR_BACKGROUND = (26, 26, 46)
COLOR_CARD_BACKGROUND = (22, 33, 62)
COLOR_PRIMARY = (0, 212, 255)
COLOR_UP = (231, 76, 60)
COLOR_DOWN = (46, 204, 113)
COLOR_WARNING = (255, 193, 7)
COLOR_TEXT_PRIMARY = (255, 255, 255)
COLOR_TEXT_SECONDARY = (176, 176, 176)
COLOR_BORDER = (45, 58, 90)

COLOR_SUCCESS = COLOR_UP
COLOR_DANGER = COLOR_DOWN