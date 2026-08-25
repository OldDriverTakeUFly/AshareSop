"""Core utilities and configuration for StockHot-CN."""

from stockhot.core import models  # noqa: F401
from stockhot.core.datasource import fetch_with_fallback, fetch_tushare_only  # noqa: F401
