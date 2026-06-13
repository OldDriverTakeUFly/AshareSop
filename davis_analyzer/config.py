"""Configuration and constants for Davis Analyzer."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env")

CACHE_DIR = PROJECT_ROOT / "davis_analyzer" / "cache"
STUDIES_DIR = PROJECT_ROOT / "davis_analyzer" / "studies"

CACHE_DIR.mkdir(parents=True, exist_ok=True)
STUDIES_DIR.mkdir(parents=True, exist_ok=True)

CACHE_DB_PATH = CACHE_DIR / "davis_analyzer.db"


def get_tushare_token() -> str:
    """Read Tushare Pro API token from environment variables (.env).

    Raises:
        EnvironmentError: If TUSHARE_TOKEN is not set.
    """
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise EnvironmentError(
            "TUSHARE_TOKEN not found. Set it in .env or as an environment variable."
        )
    return token
