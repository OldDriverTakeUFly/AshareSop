"""News/event source helpers for hotspot discovery.

Current first-wave implementation is intentionally conservative: it normalizes
curated evidence packs plus a small set of public news/policy inputs (currently
MIIT RSS listing HTML, gov.cn latest-policy JSON, STCN fast-news HTML, and the
NDRC notice page HTML) into a common news-event structure so hotspot discovery
can reason about event-backed themes without claiming full real-time ingestion yet.
"""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any
from urllib.parse import urljoin

import requests

from stockhot.research_report.evidence import iter_curated_evidence_packs

MIIT_RSS_PAGE_URL = "https://www.miit.gov.cn/RRSdy/index.html"
GOV_CN_POLICY_JSON_URL = "https://www.gov.cn/zhengce/zuixin/ZUIXINZHENGCE.json"
STCN_KUAIXUN_URL = "https://kuaixun.stcn.com/"
NDRC_NOTICE_URL = "https://www.ndrc.gov.cn/xwdt/tzgg/"


def collect_curated_news_events() -> list[dict[str, Any]]:
    """Normalize curated evidence packs into event records."""
    events: list[dict[str, Any]] = []
    for pack in iter_curated_evidence_packs():
        theme = str(pack.get("theme") or "").strip()
        if not theme:
            continue
        aliases = [str(alias).strip() for alias in pack.get("aliases", []) if str(alias).strip()]
        for item in pack.get("catalysts", []):
            events.append(
                {
                    "theme": theme,
                    "aliases": aliases,
                    "date": item.get("date", ""),
                    "source": item.get("source", ""),
                    "tier": item.get("tier", ""),
                    "title": item.get("title", ""),
                    "summary": item.get("summary", ""),
                    "mode": "curated-public-evidence",
                }
            )
    return events


def collect_miit_public_news_events(target_date: str | None = None) -> list[dict[str, Any]]:
    """Collect recent public news items from the MIIT RSS listing page.

    This is intentionally conservative and page-based, not a hidden API call.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.miit.gov.cn/",
    }
    response = requests.get(MIIT_RSS_PAGE_URL, headers=headers, timeout=20)
    response.raise_for_status()

    normalized_md = None
    if target_date:
        try:
            normalized_md = datetime.strptime(target_date, "%Y-%m-%d").strftime("%m-%d")
        except ValueError:
            normalized_md = None

    aliases_to_theme = _build_alias_theme_index()
    events: list[dict[str, Any]] = []
    pattern = re.compile(
        r'<li>\s*<a href="(?P<url>[^"]+)"[^>]*title="(?P<title>[^"]+)"[^>]*>.*?</a>\s*<span>(?P<md>\d{2}-\d{2})</span>',
        re.S,
    )

    for match in pattern.finditer(response.text):
        mmdd = match.group("md")
        if normalized_md and mmdd != normalized_md:
            continue
        title = match.group("title").strip()
        url = match.group("url").strip()
        theme, aliases = _match_theme_from_text(title, aliases_to_theme)
        events.append(
            {
                "theme": theme,
                "aliases": aliases,
                "date": _coerce_mmdd_to_date(mmdd, target_date),
                "source": "工信部RSS页",
                "tier": "一级证据",
                "title": title,
                "summary": title,
                "url": url,
                "mode": "miit-public-page",
            }
        )
    return events


def collect_news_events(target_date: str | None = None) -> list[dict[str, Any]]:
    """Collect all normalized first-wave event inputs.

    Returns curated evidence events plus first-wave public news/policy events.
    """
    events = collect_curated_news_events()
    try:
        events.extend(collect_miit_public_news_events(target_date=target_date))
    except requests.RequestException:
        pass
    try:
        events.extend(collect_gov_cn_policy_events(target_date=target_date))
    except requests.RequestException:
        pass
    try:
        events.extend(collect_stcn_kuaixun_events(target_date=target_date))
    except requests.RequestException:
        pass
    try:
        events.extend(collect_ndrc_notice_events(target_date=target_date))
    except requests.RequestException:
        pass
    return events


def collect_ndrc_notice_events(target_date: str | None = None) -> list[dict[str, Any]]:
    """Collect recent notice items from NDRC's public notice page."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.ndrc.gov.cn/",
    }
    response = requests.get(NDRC_NOTICE_URL, headers=headers, timeout=20)
    response.raise_for_status()

    aliases_to_theme = _build_alias_theme_index()
    events: list[dict[str, Any]] = []
    normalized_date = None
    if target_date:
        try:
            normalized_date = datetime.strptime(target_date, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            normalized_date = None

    pattern = re.compile(
        r'<li>\s*<a href="(?P<url>[^"]+)"[^>]*title="(?P<title>[^"]+)"[^>]*>.*?</a>\s*<span>(?P<date>\d{4}/\d{2}/\d{2})</span>',
        re.S,
    )

    for match in pattern.finditer(response.text):
        date_str = match.group("date").replace("/", "-")
        if normalized_date and date_str != normalized_date:
            continue
        title = match.group("title").strip()
        if not title:
            continue
        theme, aliases = _match_theme_from_text(title, aliases_to_theme)
        events.append(
            {
                "theme": theme,
                "aliases": aliases,
                "date": date_str,
                "source": "国家发改委通知",
                "tier": "一级证据",
                "title": title,
                "summary": title,
                "url": urljoin(NDRC_NOTICE_URL, match.group("url").strip()),
                "mode": "ndrc-notice-page",
            }
        )
    return events


def collect_gov_cn_policy_events(target_date: str | None = None) -> list[dict[str, Any]]:
    """Collect recent policy/news items from the gov.cn latest policy JSON feed."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.gov.cn/zhengce/zuixin/",
    }
    response = requests.get(GOV_CN_POLICY_JSON_URL, headers=headers, timeout=20)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        return []

    aliases_to_theme = _build_alias_theme_index()
    events: list[dict[str, Any]] = []
    normalized_date = None
    if target_date:
        try:
            normalized_date = datetime.strptime(target_date, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            normalized_date = None

    for item in payload:
        if not isinstance(item, dict):
            continue
        date_str = str(item.get("DOCRELPUBTIME") or "").strip()
        if normalized_date and date_str != normalized_date:
            continue
        title = str(item.get("TITLE") or "").strip()
        if not title:
            continue
        theme, aliases = _match_theme_from_text(title, aliases_to_theme)
        events.append(
            {
                "theme": theme,
                "aliases": aliases,
                "date": date_str,
                "source": "中国政府网最新政策",
                "tier": "一级证据",
                "title": title,
                "summary": title,
                "url": str(item.get("URL") or "").strip(),
                "mode": "govcn-policy-json",
            }
        )
    return events


def collect_stcn_kuaixun_events(target_date: str | None = None) -> list[dict[str, Any]]:
    """Collect recent quick-news items from the STCN public fast-news page."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.stcn.com/",
    }
    response = requests.get(STCN_KUAIXUN_URL, headers=headers, timeout=20)
    response.raise_for_status()

    aliases_to_theme = _build_alias_theme_index()
    events: list[dict[str, Any]] = []
    normalized_date = None
    if target_date:
        try:
            normalized_date = datetime.strptime(target_date, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            normalized_date = None

    pattern = re.compile(
        r'<li[^>]*>\s*<i>(?P<time>[^<]+)</i>\s*<a href="(?P<url>[^"]+)"[^>]*title="(?P<title>[^"]+)"[^>]*>.*?</a>\s*<span>(?P<date>\d{4}-\d{2}-\d{2})</span>',
        re.S,
    )

    for match in pattern.finditer(response.text):
        date_str = match.group("date").strip()
        if normalized_date and date_str != normalized_date:
            continue
        title = match.group("title").strip()
        if not title:
            continue
        theme, aliases = _match_theme_from_text(title, aliases_to_theme)
        events.append(
            {
                "theme": theme,
                "aliases": aliases,
                "date": date_str,
                "time": match.group("time").strip(),
                "source": "证券时报快讯",
                "tier": "辅助证据",
                "title": title,
                "summary": title,
                "url": urljoin(STCN_KUAIXUN_URL, match.group("url").strip()),
                "mode": "stcn-kuaixun-page",
            }
        )
    return events


def _build_alias_theme_index() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for pack in iter_curated_evidence_packs():
        theme = str(pack.get("theme") or "").strip()
        if not theme:
            continue
        mapping[theme] = theme
        for alias in pack.get("aliases", []):
            normalized = str(alias).strip()
            if normalized:
                mapping[normalized] = theme
    return mapping


def _match_theme_from_text(text: str, aliases_to_theme: dict[str, str]) -> tuple[str, list[str]]:
    matched_aliases = [alias for alias in aliases_to_theme if alias in text]
    if not matched_aliases:
        return "", []
    primary = aliases_to_theme[matched_aliases[0]]
    return primary, matched_aliases


def _coerce_mmdd_to_date(mmdd: str, target_date: str | None) -> str:
    month, day = mmdd.split("-")
    year = datetime.now().year
    if target_date:
        try:
            year = datetime.strptime(target_date, "%Y-%m-%d").year
        except ValueError:
            pass
    return f"{year}-{month}-{day}"
