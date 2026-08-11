"""Logging configuration for StockHot-CN."""

import os
import sys
from pathlib import Path

from loguru import logger

logger.remove()
logger.add(
    sys.stderr,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
    level="INFO",
)

# 日志目录可由 STOCKHOT_LOG_DIR 环境变量覆盖（默认 "logs"，相对 cwd）。
# 解决多身份共用同一日志文件的权限冲突：例如 08:00 数据抓取以 root 身份
# 创建 logs/stockhot_*.log 后，leo 身份的进程 import 时追加写入会 EACCES。
# 让不同身份/进程指向各自可写的目录即可隔离（默认行为不变）。
_LOG_DIR = os.environ.get("STOCKHOT_LOG_DIR", "logs")

logger.add(
    str(Path(_LOG_DIR) / "stockhot_{time:YYYY-MM-DD}.log"),
    rotation="1 day",
    retention="30 days",
    level="DEBUG",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function} - {message}",
)

logger.add(
    str(Path(_LOG_DIR) / "error_{time:YYYY-MM-DD}.log"),
    rotation="1 day",
    retention="90 days",
    level="ERROR",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function} - {message}",
)
