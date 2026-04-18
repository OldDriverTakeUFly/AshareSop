"""Storage module for StockHot-CN."""

from stockhot.storage.database import (
    init_database,
    save_daily_data,
    get_daily_data,
    cleanup_old_data,
    save_analysis_result,
    get_analysis_result,
    save_image_path,
    get_images_by_date,
)

__all__ = [
    "init_database",
    "save_daily_data",
    "get_daily_data",
    "cleanup_old_data",
    "save_analysis_result",
    "get_analysis_result",
    "save_image_path",
    "get_images_by_date",
]