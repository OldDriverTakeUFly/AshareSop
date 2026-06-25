"""Tushare API configuration and initialization.

The tushare library's default endpoint (``http://api.waditu.com/dataapi``)
appends ``/{api_name}`` to the URL, which times out on the new
``api.tushare.pro`` endpoint. The new endpoint expects POST to
``api.tushare.pro/dataapi`` with ``api_name`` in the JSON body only.

This module provides ``get_pro_api()`` that returns a properly configured
client pointing to the working endpoint.

Usage::

    from stockhot.tushare_config import get_pro_api

    pro = get_pro_api()  # uses TUSHARE_TOKEN from .env
    df = pro.daily_basic(ts_code="000001.SZ", trade_date="20260625")
"""
from __future__ import annotations

import json
import os
from functools import partial
from typing import Any

import pandas as pd
import requests
from dotenv import load_dotenv

from stockhot.core.logging import logger

_NEW_HTTP_URL = "http://api.tushare.pro/dataapi"


class _ProApi:
    """Tushare-compatible API client using the new endpoint.

    Mimics ``ts.pro_api()``: any attribute access returns a callable
    that routes to ``query(attr_name, **kwargs)``. POSTs to the new
    ``api.tushare.pro/dataapi`` endpoint without appending ``/{api_name}``.
    """

    def __init__(self, token: str, timeout: int = 60):
        self._token = token
        self._url = _NEW_HTTP_URL
        self._timeout = timeout

    def query(self, api_name: str, fields: str = "", **kwargs) -> pd.DataFrame:
        req_params: dict[str, Any] = {
            "api_name": api_name,
            "token": self._token,
            "params": kwargs,
            "fields": fields if isinstance(fields, str) else ",".join(fields),
        }
        try:
            res = requests.post(
                self._url, json=req_params, timeout=self._timeout
            )
            if not res:
                return pd.DataFrame()
            result = res.json()
            if result.get("code") != 0:
                raise Exception(result.get("msg", "unknown error"))
            data = result["data"]
            return pd.DataFrame(data["items"], columns=data["fields"])
        except requests.RequestException as e:
            logger.warning(f"Tushare {api_name} request failed: {e}")
            return pd.DataFrame()
        except Exception as e:
            if "权限" in str(e) or "token" in str(e):
                logger.warning(f"Tushare {api_name}: {e}")
            else:
                logger.warning(f"Tushare {api_name} error: {e}")
            return pd.DataFrame()

    def __getattr__(self, name: str):
        return partial(self.query, name)


def get_pro_api(timeout: int = 60) -> _ProApi:
    """Return a Tushare API client configured for the new endpoint.

    Reads the token from ``TUSHARE_TOKEN`` env var (via ``.env``).
    Uses ``api.tushare.pro/dataapi`` (the new stable endpoint).

    Args:
        timeout: Request timeout in seconds (default 60).

    Returns:
        A client object compatible with ``ts.pro_api()`` usage
        (``pro.daily_basic(...)`` style).
    """
    load_dotenv(override=True)
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise ValueError("TUSHARE_TOKEN not found in .env or environment")
    logger.info(f"tushare pro_api configured: {_NEW_HTTP_URL} (token={token[:12]}...)")
    return _ProApi(token=token, timeout=timeout)
