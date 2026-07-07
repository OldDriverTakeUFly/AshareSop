"""Tushare safe-call wrapper — unified, retryable, rate-limited Tushare access.

与 ``safe_akshare_call`` 平行的 Tushare 版本。所有 Tushare 调用应通过本模块，
确保：①统一走新端点 ``api.tushare.pro/dataapi``（绕过旧版 waditu.com 超时问题）
②本地限频 ③网络错误重试 ④权限/token 错误立即失败 ⑤空数据返回空 DataFrame（不抛异常）。

设计原则：
- Tushare 错误模式与 AKShare 不同：
  - 网络错误（requests.RequestException）：可重试（线性退避）
  - 接口返回 code != 0：分两种——含"权限"/"token"= 立即失败不重试；其他（如频率限制）= 可重试
  - 返回空 DataFrame：视为"成功但无数据"，不重试（与 AKShare 一致）
- 限频：Tushare 限制 500 次/min（约 8.3/s），本地保守 5/s（limiter 间隔 0.2s）
- 与 ``get_pro_api()`` 的关系：本模块直接 POST 新端点，不依赖 ``_ProApi.query``
  （因为 query 吞掉了 RequestException，无法作为重试触发条件）
"""

from __future__ import annotations

import os
import time
import threading
from typing import Any

import pandas as pd
import requests
from dotenv import load_dotenv

from stockhot.core.logging import logger
from stockhot.core.rate_limiter import RateLimiter

# ── 常量 ──────────────────────────────────────────────────────────────

_NEW_HTTP_URL = "http://api.tushare.pro/dataapi"
_MAX_RETRIES = 2
_RETRY_BASE_DELAY = 1.0  # 线性退避：1s, 2s

# 本地限频器（线程安全单例）。Tushare 限制 500/min ≈ 8.3/s，保守设 5/s。
_tushare_limiter = RateLimiter(calls_per_second=5.0)
_token_cache: str | None = None
_token_lock = threading.Lock()


def _get_token() -> str:
    """从 .env 读取 TUSHARE_TOKEN（带缓存，线程安全）。"""
    global _token_cache
    if _token_cache:
        return _token_cache
    with _token_lock:
        if _token_cache:
            return _token_cache
        load_dotenv(override=True)
        token = os.environ.get("TUSHARE_TOKEN", "")
        if not token:
            raise ValueError("TUSHARE_TOKEN not found in .env or environment")
        _token_cache = token
        return token


def safe_tushare_call(api_name: str, fields: str = "", **params) -> pd.DataFrame:
    """统一 Tushare 调用：新端点 + 限频 + 重试 + 空检查。

    参数：
        api_name: Tushare 接口名，如 "limit_list_d"、"top_list"、"daily_basic"
        fields: 字段过滤（逗号分隔字符串），空则返回全部字段
        **params: 接口参数，如 trade_date="20260707"、limit="U"

    返回：
        成功返回 DataFrame；失败/无数据返回空 DataFrame（**永不抛异常**）。

    重试策略：
        - requests.RequestException（网络错误）：重试 _MAX_RETRIES 次，线性退避
        - 接口返回 code != 0 且 msg 含"权限"/"token"：立即失败，不重试
        - 接口返回 code != 0 且不含上述关键词（如频率限制）：重试
        - 返回空 DataFrame：视为成功无数据，不重试
    """
    try:
        token = _get_token()
    except ValueError as e:
        logger.warning(f"Tushare {api_name}: {e}")
        return pd.DataFrame()

    req_params: dict[str, Any] = {
        "api_name": api_name,
        "token": token,
        "params": params,
        "fields": fields if isinstance(fields, str) else ",".join(fields),
    }

    last_error: str | None = None
    for attempt in range(_MAX_RETRIES + 1):  # 1 初始 + _MAX_RETRIES 重试
        try:
            if attempt > 0:
                delay = _RETRY_BASE_DELAY * attempt
                logger.info(
                    f"Tushare retry {attempt}/{_MAX_RETRIES} for {api_name} "
                    f"after {delay:.0f}s (last error: {last_error})"
                )
                time.sleep(delay)
            _tushare_limiter.acquire()

            res = requests.post(_NEW_HTTP_URL, json=req_params, timeout=30)
            if not res:
                last_error = "empty response"
                continue
            result = res.json()
            code = result.get("code")
            if code != 0:
                msg = result.get("msg", "unknown error")
                last_error = msg
                # 权限/token 错误：立即失败，不重试
                if "权限" in msg or "token" in msg:
                    logger.warning(f"Tushare {api_name}: {msg} (no retry - permission error)")
                    return pd.DataFrame()
                # 其他错误（频率限制等）：继续重试
                continue

            data = result.get("data")
            if not data or not data.get("items"):
                # 成功但无数据
                logger.info(f"Tushare {api_name}: no data for params={params}")
                return pd.DataFrame()

            df = pd.DataFrame(data["items"], columns=data["fields"])
            logger.info(f"Tushare {api_name}: {len(df)} rows")
            return df

        except requests.RequestException as e:
            # 网络错误：可重试
            last_error = f"{type(e).__name__}: {e}"
            logger.warning(f"Tushare {api_name} network error (attempt {attempt}): {last_error}")
            continue
        except Exception as e:
            # 未知错误：不重试，记录后返回空
            logger.warning(f"Tushare {api_name} unexpected error: {type(e).__name__}: {e}")
            return pd.DataFrame()

    # 重试耗尽
    logger.warning(f"Tushare {api_name} failed after {_MAX_RETRIES} retries: {last_error}")
    return pd.DataFrame()
