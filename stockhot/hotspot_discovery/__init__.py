"""Hotspot discovery module for StockHot-CN.

Current behavior is sample+public-news v2.5: it discovers hotspot candidates from
current sectors, THS fund-flow samples, strong-stock samples, curated public
evidence packs, and a first-wave public news/policy input layer (currently MIIT
RSS listing HTML + gov.cn latest policy JSON), then stores a structured
`hotspot_discovery` payload for downstream use.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from stockhot.core.utils import fund_flow_scope_label, safe_float
from stockhot.data_collector.sector_map import get_stock_sector
from stockhot.hotspot_discovery.news_sources import collect_news_events
from stockhot.research_report.evidence import iter_curated_evidence_packs
from stockhot.storage.database import get_daily_data, save_analysis_result


def run_hotspot_discovery(date: str | None = None) -> dict:
    """Run hotspot discovery for a trading date."""
    target_date = date or datetime.now().strftime("%Y-%m-%d")
    print(f"[HotspotDiscovery] 发现日期: {target_date}")

    market_data = get_daily_data(target_date)
    if not market_data or not _has_discoverable_data(market_data):
        print("[HotspotDiscovery] 无样本可发现热点")
        return {"date": target_date, "status": "no_data"}

    payload = build_hotspot_discovery(market_data, target_date=target_date)
    save_analysis_result(target_date, "hotspot_discovery", payload)

    print("[HotspotDiscovery] 发现完成")
    return {
        "date": target_date,
        "status": "success",
        "count": len(payload["themes"]),
        "lead_theme": payload["lead_theme"],
    }


def build_hotspot_discovery(
    market_data: dict[str, Any], target_date: str | None = None
) -> dict[str, Any]:
    sectors = market_data.get("sectors", [])
    fund_flows = market_data.get("fund_flows", [])
    gainers = market_data.get("gainers", [])
    raw_news_events = collect_news_events(target_date=target_date)
    news_events = [event for event in raw_news_events if event.get("theme")]
    news_event_clusters = _cluster_news_events(news_events)

    candidates: dict[str, dict[str, Any]] = {}

    for item in sectors[:10]:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        entry = candidates.setdefault(name, _empty_candidate(name))
        entry["matched_sectors"].append(
            {
                "name": name,
                "change_pct": safe_float(item.get("change_pct")),
                "leader_stock": str(item.get("leader_stock") or ""),
            }
        )

    for item in fund_flows[:10]:
        theme_name = str(item.get("name") or "").strip()
        if not theme_name:
            continue
        entry = candidates.setdefault(theme_name, _empty_candidate(theme_name))
        entry["matched_fund_flows"].append(
            {
                "name": theme_name,
                "net_inflow": safe_float(item.get("net_inflow")),
                "scope": fund_flow_scope_label(item),
                "leader_stock": str(item.get("leader_stock") or ""),
            }
        )

    for item in gainers[:20]:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        sector = get_stock_sector(name)
        if not sector or sector == "其他":
            continue
        entry = candidates.setdefault(sector, _empty_candidate(sector))
        entry["matched_stocks"].append(
            {
                "name": name,
                "code": str(item.get("code") or ""),
                "change_pct": safe_float(item.get("change_pct")),
            }
        )

    _merge_news_event_matches(candidates, news_event_clusters)
    _merge_curated_evidence(candidates)
    event_theme_candidates = _build_event_theme_candidates(news_event_clusters)

    themes = []
    for theme_name, candidate in candidates.items():
        sector_score = 0.5 if candidate["matched_sectors"] else 0.0
        fund_score = 0.3 if candidate["matched_fund_flows"] else 0.0
        stock_score = 0.2 if candidate["matched_stocks"] else 0.0
        evidence_score = 0.25 if candidate["news_signals"] else 0.0
        confidence_score = sector_score + fund_score + stock_score + evidence_score
        confidence = (
            "high" if confidence_score >= 0.8 else "medium" if confidence_score >= 0.5 else "low"
        )
        summary = _build_theme_summary(theme_name, candidate)
        themes.append(
            {
                "name": theme_name,
                "source_mode": _source_mode(candidate),
                "confidence": confidence,
                "confidence_score": round(confidence_score, 2),
                "matched_sectors": candidate["matched_sectors"],
                "matched_fund_flows": candidate["matched_fund_flows"],
                "matched_stocks": candidate["matched_stocks"],
                "news_signals": candidate["news_signals"],
                "evidence_sources": candidate["evidence_sources"],
                "summary": summary,
            }
        )

    themes.sort(
        key=lambda item: (
            item["confidence_score"],
            _first_sector_change(item),
            _first_net_inflow(item),
        ),
        reverse=True,
    )

    lead_theme = themes[0]["name"] if themes else None
    theme_clusters = _build_theme_clusters(themes, event_theme_candidates)
    return {
        "lead_theme": lead_theme,
        "themes": themes,
        "theme_clusters": theme_clusters,
        "news_events": news_events,
        "raw_news_events": raw_news_events,
        "news_event_clusters": news_event_clusters,
        "event_theme_candidates": event_theme_candidates,
        "event_backed_themes": [item["name"] for item in themes if item["news_signals"]],
        "method": "sample+public-news-v2.5",
        "limitations": [
            "当前版本以板块样本、THS资金样本、强势股样本和第一波公开新闻页输入为主，并可叠加少量人工整理的公开资料证据包。",
            "若缺少更完整的实时新闻流、公告流与事件归因，本结果仍不能替代完整的事件级主题发现。",
        ],
    }


def _empty_candidate(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "matched_sectors": [],
        "matched_fund_flows": [],
        "matched_stocks": [],
        "news_signals": [],
        "evidence_sources": [],
    }


def _build_theme_summary(theme_name: str, candidate: dict[str, Any]) -> str:
    parts = []
    if candidate["news_signals"]:
        top_signal = max(candidate["news_signals"], key=_event_sort_key)
        parts.append(f"公开资料中，{top_signal['title']}。")
    if candidate["matched_sectors"]:
        top_sector = candidate["matched_sectors"][0]
        parts.append(f"板块样本中，{theme_name}涨幅{top_sector['change_pct']:+.2f}%。")
    if candidate["matched_fund_flows"]:
        top_flow = candidate["matched_fund_flows"][0]
        direction = "净流入" if top_flow["net_inflow"] >= 0 else "净流出"
        parts.append(
            f"{top_flow['scope'] or '资金'}样本中，{theme_name}{direction}{abs(top_flow['net_inflow']):.2f}亿。"
        )
    if candidate["matched_stocks"]:
        top_stock = candidate["matched_stocks"][0]
        parts.append(f"强势股样本中，{top_stock['name']}涨幅{top_stock['change_pct']:+.2f}%。")
    if not parts:
        parts.append(f"当前仅检出“{theme_name}”的零散样本线索。")
    return " ".join(parts)


def _cluster_news_events(news_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for event in news_events:
        key = _event_cluster_key(event)
        grouped.setdefault(key, []).append(event)

    clusters = []
    for _, events in grouped.items():
        representative = max(events, key=_event_sort_key)
        clusters.append(
            {
                "theme": representative.get("theme", ""),
                "aliases": representative.get("aliases", []),
                "date": representative.get("date", ""),
                "source": representative.get("source", ""),
                "tier": representative.get("tier", ""),
                "title": representative.get("title", ""),
                "summary": representative.get("summary", ""),
                "url": representative.get("url", ""),
                "mode": representative.get("mode", ""),
                "member_count": len(events),
                "members": events,
            }
        )
    clusters.sort(key=_event_sort_key, reverse=True)
    return clusters


def _event_cluster_key(event: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(event.get("theme") or "").strip(),
        str(event.get("source") or "").strip(),
        str(event.get("title") or "").strip(),
    )


def _build_event_theme_candidates(news_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in news_events:
        theme = str(event.get("theme") or "").strip()
        if not theme:
            continue
        grouped.setdefault(theme, []).append(event)

    candidates = []
    for theme, events in grouped.items():
        latest = max(events, key=_event_sort_key)
        candidates.append(
            {
                "theme": theme,
                "aliases": latest.get("aliases", []),
                "latest_event": {
                    "date": latest.get("date", ""),
                    "source": latest.get("source", ""),
                    "title": latest.get("title", ""),
                    "tier": latest.get("tier", ""),
                },
            }
        )
    candidates.sort(key=lambda item: _event_sort_key(item["latest_event"]), reverse=True)
    return candidates


def _event_sort_key(event: dict[str, Any]) -> tuple[int, str]:
    date_str = str(event.get("date") or "")
    try:
        parsed = datetime.strptime(date_str, "%Y-%m-%d")
        return (1, parsed.strftime("%Y-%m-%d"))
    except ValueError:
        return (0, date_str)


def _merge_curated_evidence(candidates: dict[str, dict[str, Any]]) -> None:
    for evidence in iter_curated_evidence_packs():
        theme_name = str(evidence.get("theme") or "").strip()
        if not theme_name:
            continue
        candidate = candidates.setdefault(theme_name, _empty_candidate(theme_name))
        for signal in [
            {
                "date": item.get("date", ""),
                "source": item.get("source", ""),
                "tier": item.get("tier", ""),
                "title": item.get("title", ""),
            }
            for item in evidence.get("catalysts", [])[:4]
        ]:
            if signal not in candidate["news_signals"]:
                candidate["news_signals"].append(signal)
        for source_entry in [
            {
                "tier": tier,
                "items": items,
            }
            for tier, items in evidence.get("source_tiers", {}).items()
        ]:
            existing = next(
                (
                    entry
                    for entry in candidate["evidence_sources"]
                    if entry.get("tier") == source_entry["tier"]
                ),
                None,
            )
            if existing is None:
                candidate["evidence_sources"].append(source_entry)
                continue
            for item in source_entry["items"]:
                if item not in existing["items"]:
                    existing["items"].append(item)


def _merge_news_event_matches(
    candidates: dict[str, dict[str, Any]], news_events: list[dict[str, Any]]
) -> None:
    for event in news_events:
        theme_name = str(event.get("theme") or "").strip()
        if not theme_name:
            continue
        candidate = candidates.setdefault(theme_name, _empty_candidate(theme_name))
        signal = {
            "date": event.get("date", ""),
            "source": event.get("source", ""),
            "tier": event.get("tier", ""),
            "title": event.get("title", ""),
        }
        if signal not in candidate["news_signals"]:
            candidate["news_signals"].append(signal)

        source_entry = {
            "tier": event.get("tier", ""),
            "items": [f"{event.get('source', '')}：{event.get('title', '')}"],
        }
        existing = next(
            (
                entry
                for entry in candidate["evidence_sources"]
                if entry.get("tier") == source_entry["tier"]
            ),
            None,
        )
        if existing is None:
            candidate["evidence_sources"].append(source_entry)
        elif source_entry["items"][0] not in existing["items"]:
            existing["items"].append(source_entry["items"][0])


def _source_mode(candidate: dict[str, Any]) -> str:
    has_sample = bool(
        candidate["matched_sectors"]
        or candidate["matched_fund_flows"]
        or candidate["matched_stocks"]
    )
    has_evidence = bool(candidate["news_signals"])
    if has_sample and has_evidence:
        return "sample+evidence"
    if has_evidence:
        return "evidence-only"
    return "sample-only"


def _build_theme_clusters(
    themes: list[dict[str, Any]], event_theme_candidates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    event_alias_map = {
        str(item.get("theme") or ""): [
            str(alias) for alias in item.get("aliases", []) if str(alias).strip()
        ]
        for item in event_theme_candidates
        if item.get("theme")
    }

    clusters = []
    for theme in themes:
        aliases = _collect_theme_aliases(theme, event_alias_map.get(theme["name"], []))
        cluster = {
            "canonical_theme": _normalize_theme_key(theme["name"]),
            "aliases": aliases,
            "confidence": theme["confidence"],
            "confidence_score": theme["confidence_score"],
            "source_mode": theme["source_mode"],
            "signal_counts": {
                "sectors": len(theme["matched_sectors"]),
                "fund_flows": len(theme["matched_fund_flows"]),
                "stocks": len(theme["matched_stocks"]),
                "news": len(theme["news_signals"]),
                "evidence": len(theme["evidence_sources"]),
            },
            "supporting_sources": _build_supporting_sources(theme),
            "cluster_summary": _build_cluster_summary(theme),
            "matched_sectors": theme["matched_sectors"],
            "matched_fund_flows": theme["matched_fund_flows"],
            "matched_stocks": theme["matched_stocks"],
            "news_signals": theme["news_signals"],
            "evidence_sources": theme["evidence_sources"],
        }
        clusters.append(cluster)

    return clusters


def _normalize_theme_key(name: str) -> str:
    return str(name or "").strip().strip("：:")


def _collect_theme_aliases(theme: dict[str, Any], event_aliases: list[str]) -> list[str]:
    aliases: list[str] = []
    theme_name = str(theme.get("name") or "").strip()
    if theme_name:
        aliases.append(theme_name)
    for alias in event_aliases:
        if alias not in aliases:
            aliases.append(alias)
    return aliases


def _build_supporting_sources(theme: dict[str, Any]) -> list[str]:
    sources: list[str] = []
    if theme.get("matched_sectors") and "板块样本" not in sources:
        sources.append("板块样本")
    if theme.get("matched_stocks") and "强势股样本" not in sources:
        sources.append("强势股样本")
    for signal in theme.get("news_signals", []):
        source = str(signal.get("source") or "").strip()
        if source and source not in sources:
            sources.append(source)
    for flow in theme.get("matched_fund_flows", []):
        scope = str(flow.get("scope") or "").strip()
        if scope and scope not in sources:
            sources.append(scope)
    return sources


def _build_cluster_summary(theme: dict[str, Any]) -> str:
    parts = []
    name = str(theme.get("name") or "")
    if theme.get("news_signals"):
        parts.append(f"{name}存在公开事件线索。")
    if theme.get("matched_sectors"):
        top_sector = theme["matched_sectors"][0]
        parts.append(f"板块样本涨幅{top_sector['change_pct']:+.2f}%。")
    if theme.get("matched_fund_flows"):
        top_flow = theme["matched_fund_flows"][0]
        direction = "净流入" if top_flow["net_inflow"] >= 0 else "净流出"
        parts.append(
            f"{top_flow['scope'] or '资金'}样本{direction}{abs(top_flow['net_inflow']):.2f}亿。"
        )
    if theme.get("matched_stocks"):
        parts.append(f"强势股样本数{len(theme['matched_stocks'])}。")
    return " ".join(parts) if parts else f"{name}当前仅有零散样本。"


def _first_sector_change(item: dict[str, Any]) -> float:
    sectors = item.get("matched_sectors") or []
    if not sectors:
        return float("-inf")
    return safe_float(sectors[0].get("change_pct"), default=float("-inf"))


def _first_net_inflow(item: dict[str, Any]) -> float:
    flows = item.get("matched_fund_flows") or []
    if not flows:
        return float("-inf")
    return safe_float(flows[0].get("net_inflow"), default=float("-inf"))


def _has_discoverable_data(data: dict[str, Any]) -> bool:
    return any(data.get(key) for key in ("sectors", "fund_flows", "gainers"))
    return default
