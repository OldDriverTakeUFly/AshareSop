"""Tushare MCP (Model Context Protocol) client.

Provides a drop-in replacement for ``ts.pro_api()`` calls that stopped
working on the legacy ``api.waditu.com`` endpoint. The MCP server at
``api.tushare.pro/mcp/`` uses the same Tushare token but a different
transport (Streamable HTTP / SSE JSON-RPC).

Usage::

    from stockhot.mcp_client import TushareMCP

    mcp = TushareMCP()               # reads TUSHARE_MCP_URL or TUSHARE_TOKEN
    df = mcp.query("daily_basic", ts_code="000001.SZ", trade_date="20260625")
    # df is a pandas DataFrame, same shape as pro.daily_basic()

Design notes:
- ``query()`` accepts the same kwargs as ``pro.<api_name>()`` (flat string
  params + optional ``fields="a,b,c"`` comma string). It internally converts
  ``fields`` to the array format MCP requires and wraps the call in a
  JSON-RPC ``tools/call``.
- Returns a ``pandas.DataFrame`` to be a drop-in for existing code that
  expects ``pro`` output.
- Includes a simple request throttle (min 2s between calls) because the MCP
  server slows down under rapid-fire requests.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import pandas as pd
import requests
from dotenv import load_dotenv

from stockhot.core.logging import logger


class TushareMCP:
    """Thin client over the Tushare MCP Streamable-HTTP endpoint.

    Attributes:
        url: Full MCP endpoint URL (includes the token as a query param).
        _last_call: Monotonic timestamp of the last request, for throttling.
    """

    _MIN_INTERVAL = 2.0  # seconds between calls (server throttles)

    def __init__(self, url: str | None = None, token: str | None = None):
        load_dotenv()
        if url:
            self.url = url
        elif env_url := os.environ.get("TUSHARE_MCP_URL"):
            self.url = env_url
        else:
            # Build from token
            tok = token or os.environ.get("TUSHARE_TOKEN", "")
            if not tok:
                raise ValueError(
                    "No TUSHARE_MCP_URL or TUSHARE_TOKEN found. "
                    "Set one in .env"
                )
            self.url = f"https://api.tushare.pro/mcp/?token={tok}"
        self._last_call: float = 0.0
        self._msg_id: int = 0
        logger.info(f"TushareMCP initialised: {self.url[:60]}...")

    # -- low-level SSE call -------------------------------------------------

    def _call(self, tool_name: str, arguments: dict[str, Any]) -> list[dict]:
        """Send a JSON-RPC tools/call and return the parsed content items."""
        self._throttle()
        self._msg_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._msg_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        try:
            r = requests.post(self.url, json=payload, headers=headers, timeout=60)
            r.raise_for_status()
        except requests.RequestException as e:
            logger.warning(f"MCP {tool_name} request failed: {e}")
            return []

        # Parse SSE response. MCP uses \r\n line endings; each "data:" line
        # is a complete JSON-RPC message (no multi-line splitting needed).
        data_lines: list[str] = []
        for line in r.text.split("\n"):
            line = line.strip("\r\n")
            if line.startswith("data: "):
                data_lines.append(line[6:])
            elif line.startswith("data:"):
                data_lines.append(line[5:])
        if not data_lines:
            logger.warning(f"MCP {tool_name}: no data line in SSE response")
            return []

        # Each data line should be a complete JSON object
        for raw in data_lines:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            else:
                break
        else:
            logger.warning(
                f"MCP {tool_name} JSON parse failed on {len(data_lines)} lines"
            )
            return []

        if data.get("error"):
            err = data["error"]
            logger.warning(
                f"MCP {tool_name} error: {err.get('message', '')[:120]}"
            )
            return []

        result = data.get("result", {})
        # MCP may return isError=true with an error message in content
        if result.get("isError"):
            for item in result.get("content", []):
                txt = item.get("text", "")
                if "权限" in txt or "error" in txt.lower():
                    logger.warning(f"MCP {tool_name}: {txt[:120]}")
            return []

        content = result.get("content", [])
        # content is a list of {"type": "text", "text": "[...]"}
        rows: list[dict] = []
        for item in content:
            text = item.get("text", "")
            if text and text.strip().startswith("["):
                try:
                    rows.extend(json.loads(text))
                except json.JSONDecodeError:
                    pass
        return rows
        return []

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self._MIN_INTERVAL:
            time.sleep(self._MIN_INTERVAL - elapsed)
        self._last_call = time.monotonic()

    # -- public API ---------------------------------------------------------

    def query(self, api_name: str, **kwargs) -> pd.DataFrame:
        """Query a Tushare data interface via MCP.

        Mirrors the signature of ``pro.<api_name>(**kwargs)``. The optional
        ``fields`` kwarg accepts either a comma-separated string (legacy
        tushare style) or a list (MCP native); both are converted to the
        array format MCP requires.

        Args:
            api_name: Tushare interface name, e.g. "daily_basic".
            **kwargs: Query parameters (ts_code, trade_date, etc.).

        Returns:
            A pandas DataFrame with the returned rows. Empty on error.
        """
        arguments = dict(kwargs)
        # Convert fields: "a,b,c" → ["a","b","c"]
        if "fields" in arguments:
            f = arguments["fields"]
            if isinstance(f, str):
                arguments["fields"] = [
                    x.strip() for x in f.split(",") if x.strip()
                ]
        # Remove None values (MCP rejects them)
        arguments = {k: v for k, v in arguments.items() if v is not None}

        rows = self._call(api_name, arguments)
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)

    # -- convenience methods for common patterns ----------------------------

    def get_pro_api(self) -> "_ProApiShim":
        """Return a shim that mimics ``ts.pro_api()`` for drop-in replacement.

        Usage::

            pro = mcp.get_pro_api()
            df = pro.daily_basic(ts_code="000001.SZ", trade_date="20260625")
        """
        return _ProApiShim(self)


class _ProApiShim:
    """Mimics the ``pro`` object from ``ts.pro_api()``.

    Any attribute access returns a callable that routes to
    ``TushareMCP.query(attr_name, **kwargs)``.
    """

    def __init__(self, mcp: TushareMCP):
        self._mcp = mcp

    def __getattr__(self, api_name: str):
        def caller(**kwargs):
            return self._mcp.query(api_name, **kwargs)
        return caller
