"""统一 Tushare 网关 — 合并 4 套客户端为 1 套.

本模块是**所有 Tushare 调用的唯一入口**，替代项目中并存的 4 套客户端：
1. ``stockhot.core.tushare_client_safe.safe_tushare_call`` — 无缓存，函数式
2. ``stockhot.tushare_config.get_pro_api`` — 无缓存，面向对象
3. ``davis_analyzer.tushare_client.TushareClient`` — 有结构化缓存，面向对象
4. 各处裸调 ``ts.set_token() + ts.pro_api()`` — 违反规范（macro/valuation/fund_flow）

融合设计（取各家之长）：
- **传输层**：统一走新端点 ``api.tushare.pro/dataapi``（stockhot 的，绕过旧版 waditu.com 超时）
- **限频**：线程安全滑窗（stockhot RateLimiter 的锁 + davis 的 400/min 上限）
- **错误分类**（继承 stockhot safe_tushare_call）：
  - 权限/token 错误 → 立即失败不重试
  - 频率限制 → 指数退避重试（davis 风格：1s/2s/4s）
  - 网络错误 → 重试
  - 空数据 → 返回空 DataFrame 不抛
- **分页支持**（davis 缺失）：全市场查询自动 offset 分页突破 5000 行限制
- **时区强制** Asia/Shanghai（修复 davis date.today() 隐患）
- **双调用风格**：
  - 函数式：``gw.call("limit_list_d", trade_date=d)`` — 兼容 stockhot
  - 面向对象：``gw.get_daily_prices(ts_code, start, end)`` — 兼容 davis（带缓存）

缓存策略委托给 ``cache.py`` 的 CacheStrategy，本模块只管传输 + 限频 + 重试。
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from dotenv import load_dotenv

from stockhot.core.logging import logger
from stockhot.core.rate_limiter import RateLimiter

# ── 常量 ──────────────────────────────────────────────────────────────

_NEW_HTTP_URL = "http://api.tushare.pro/dataapi"
_TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")

# 限频：Tushare 硬上限 500/min，davis 设 400/min 留余量。
# 用双轨限频：①滑窗 400/min（防 burst）②最小间隔 0.2s（RateLimiter，防瞬时并发）
_RATE_LIMIT_WINDOW = 60.0  # 滑窗秒数
_RATE_LIMIT_MAX = 400  # 滑窗内最大请求数
_call_limiter = RateLimiter(calls_per_second=5.0)  # 5/s 最小间隔

# 重试：指数退避（davis 风格）1s/2s/4s，共 3 次重试
_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0

# 分页：Tushare 单次最多返回约 5000-10000 行，超出需 offset 分页
_PAGE_SIZE = 5000
_MAX_PAGES = 20  # 安全上限，避免无限分页

# token 缓存
_token_cache: str | None = None
_token_lock = threading.Lock()

# 滑窗限频（线程安全）
_window_lock = threading.Lock()
_call_timestamps: deque[float] = deque()


def _get_token() -> str:
    """从 .env 读取 TUSHARE_TOKEN（带缓存，线程安全）."""
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


def _wait_for_rate_limit() -> None:
    """滑窗限频：确保 60 秒内不超过 _RATE_LIMIT_MAX 次请求（线程安全）."""
    now = time.time()
    with _window_lock:
        # 清理过期时间戳
        while _call_timestamps and now - _call_timestamps[0] >= _RATE_LIMIT_WINDOW:
            _call_timestamps.popleft()
        if len(_call_timestamps) >= _RATE_LIMIT_MAX:
            sleep_time = _call_timestamps[0] + _RATE_LIMIT_WINDOW - now + 0.1
            if sleep_time > 0:
                logger.info(f"Tushare rate limit: sleeping {sleep_time:.1f}s")
                time.sleep(sleep_time)
                # 清理后重试
                now = time.time()
                while _call_timestamps and now - _call_timestamps[0] >= _RATE_LIMIT_WINDOW:
                    _call_timestamps.popleft()
        _call_timestamps.append(time.time())


def _today_shanghai() -> date:
    """获取北京时间今天的日期（修复 davis date.today() 本地时区隐患）."""
    return datetime.now(_TZ_SHANGHAI).date()


def _next_date_str(date_str: str) -> str:
    """YYYYMMDD + 1 天（继承 davis _next_date_str，用于增量拉取）."""
    d = datetime.strptime(date_str, "%Y%m%d").date()
    return (d + pd.Timedelta(days=1)).strftime("%Y%m%d") if hasattr(pd, "Timedelta") else \
        (d.fromordinal(d.toordinal() + 1)).strftime("%Y%m%d")


class TushareGateway:
    """统一 Tushare 网关 — 传输 + 限频 + 重试 + 分页.

    用法（函数式，兼容 stockhot）::

        from stockhot.data_layer import get_gateway
        gw = get_gateway()
        df = gw.call("limit_list_d", trade_date="20260715", limit="U")

    用法（面向对象，兼容 davis，缓存由 repository 层处理）::

        df = gw.call("daily", ts_code="000001.SZ", start_date="20260101")
    """

    def __init__(self) -> None:
        self._token = _get_token()

    def call(
        self,
        api_name: str,
        fields: str = "",
        paginate: bool = False,
        **params,
    ) -> pd.DataFrame:
        """调用 Tushare 接口（函数式，兼容 stockhot safe_tushare_call）.

        参数：
            api_name: 接口名，如 "limit_list_d"、"daily"、"daily_basic"
            fields: 字段过滤（逗号分隔），空则返回全部
            paginate: 是否自动分页（全市场查询用，如 daily_basic by trade_date）
            **params: 接口参数

        返回：
            成功返回 DataFrame；失败/无数据返回**空 DataFrame（永不抛异常）**.
        """
        if paginate:
            return self._call_paginated(api_name, fields, **params)
        return self._call_single(api_name, fields, **params)

    def _call_single(self, api_name: str, fields: str, **params) -> pd.DataFrame:
        """单次调用（带限频 + 重试 + 错误分类）."""
        req_params = {
            "api_name": api_name,
            "token": self._token,
            "params": params,
            "fields": fields if isinstance(fields, str) else ",".join(fields),
        }

        last_error: str | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                if attempt > 0:
                    backoff = _BACKOFF_BASE * (2 ** (attempt - 1))
                    logger.info(
                        f"Tushare retry {attempt}/{_MAX_RETRIES} for {api_name} "
                        f"after {backoff:.0f}s (last: {last_error})"
                    )
                    time.sleep(backoff)

                # 双轨限频
                _wait_for_rate_limit()
                _call_limiter.acquire()

                res = requests.post(_NEW_HTTP_URL, json=req_params, timeout=30)
                if not res:
                    last_error = "empty response"
                    continue

                result = res.json()
                code = result.get("code")
                if code != 0:
                    msg = result.get("msg", "unknown error")
                    last_error = msg
                    # 权限/token 错误：立即失败不重试
                    if "权限" in msg or "token" in msg:
                        logger.warning(
                            f"Tushare {api_name}: {msg} (no retry - permission error)"
                        )
                        return pd.DataFrame()
                    # 其他错误（频率限制等）：继续重试
                    continue

                data = result.get("data")
                if not data or not data.get("items"):
                    logger.info(f"Tushare {api_name}: no data for {params}")
                    return pd.DataFrame()

                df = pd.DataFrame(data["items"], columns=data["fields"])
                logger.info(f"Tushare {api_name}: {len(df)} rows")
                return df

            except requests.RequestException as e:
                last_error = f"{type(e).__name__}: {e}"
                logger.warning(
                    f"Tushare {api_name} network error (attempt {attempt}): {last_error}"
                )
                continue
            except Exception as e:
                logger.warning(f"Tushare {api_name} error: {type(e).__name__}: {e}")
                return pd.DataFrame()

        logger.warning(
            f"Tushare {api_name} failed after {_MAX_RETRIES} retries: {last_error}"
        )
        return pd.DataFrame()

    def _call_paginated(self, api_name: str, fields: str, **params) -> pd.DataFrame:
        """分页调用 — 突破 5000 行限制（全市场查询用）.

        通过 offset 参数逐页拉取，直到某页返回 < _PAGE_SIZE 行或达到 _MAX_PAGES。
        仅对支持 offset 的接口有效（daily/daily_basic/moneyflow 等按 trade_date 查询的）。
        """
        all_dfs: list[pd.DataFrame] = []
        for page in range(_MAX_PAGES):
            page_params = {**params, "offset": page * _PAGE_SIZE, "limit": _PAGE_SIZE}
            df = self._call_single(api_name, fields, **page_params)
            if df.empty:
                break
            all_dfs.append(df)
            if len(df) < _PAGE_SIZE:
                break  # 最后一页
        if not all_dfs:
            return pd.DataFrame()
        result = pd.concat(all_dfs, ignore_index=True)
        logger.info(f"Tushare {api_name} paginated: {len(result)} rows ({len(all_dfs)} pages)")
        return result

    # ── 便捷方法（面向对象风格，缓存由 repository 层处理）──

    def get_stock_list(self) -> pd.DataFrame:
        """获取 A 股全量股票列表（stock_basic）."""
        return self.call("stock_basic", exchange="", list_status="L",
                         fields="ts_code,name,industry,list_status")

    def get_daily_by_date(self, trade_date: str) -> pd.DataFrame:
        """按交易日获取全市场个股行情（分页）."""
        return self.call(
            "daily", trade_date=trade_date, paginate=True,
            fields="ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount",
        )

    def get_adj_factor(self, trade_date: str) -> pd.DataFrame:
        """按交易日获取全市场复权因子（分页）."""
        return self.call(
            "adj_factor", trade_date=trade_date, paginate=True,
            fields="ts_code,trade_date,adj_factor",
        )

    # ── SDK 风格属性代理 ──────────────────────────────────────────────

    def __getattr__(self, api_name: str):
        """属性访问代理：让 ``gw.cn_pmi(start_m=...)`` 等价于 ``gw.call("cn_pmi", ...)``.

        替代旧的 ``ts.set_token() + ts.pro_api()`` SDK 用法，使 macro/valuation/
        fund_flow 等模块只需把 ``_get_pro_api()`` 换成 ``get_gateway()``，
        其余 ``pro.cn_pmi(...)`` 风格代码无需改动。

        注意：__getattr__ 只在常规属性查找失败时触发，不会遮蔽 call/get_* 等显式方法。
        """
        # 返回一个可调用对象，调用时转发到 self.call
        def _proxy(**params) -> pd.DataFrame:
            return self.call(api_name, **params)
        _proxy.__name__ = api_name
        return _proxy


# ── 模块级单例 ───────────────────────────────────────────────────────

_gateway_instance: TushareGateway | None = None
_gateway_lock = threading.Lock()


def get_gateway() -> TushareGateway:
    """获取 TushareGateway 单例（线程安全）."""
    global _gateway_instance
    if _gateway_instance:
        return _gateway_instance
    with _gateway_lock:
        if _gateway_instance:
            return _gateway_instance
        _gateway_instance = TushareGateway()
        return _gateway_instance


def reset_gateway() -> None:
    """重置单例（测试用）."""
    global _gateway_instance
    with _gateway_lock:
        _gateway_instance = None
