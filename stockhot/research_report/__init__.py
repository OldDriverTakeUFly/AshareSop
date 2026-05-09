"""Theme research report module for StockHot-CN."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from stockhot.core.config import get_reports_dir_for_date
from stockhot.core.utils import fund_flow_direction_phrase, fund_flow_scope_label, safe_float
from stockhot.data_collector.sector_map import get_stock_sector
from stockhot.research_report.evidence import (
    get_curated_theme_evidence,
    normalize_theme_with_evidence_pack,
)
from stockhot.storage.database import (
    get_daily_data,
    get_preferred_analysis_result,
    save_analysis_result,
)


def run_research_report(
    date: str | None = None,
    theme: str | None = None,
    catalyst: str | None = None,
) -> dict:
    """Generate a theme research summary and persist it."""
    target_date = date or datetime.now().strftime("%Y-%m-%d")
    print(f"[ResearchReport] 研报日期: {target_date}")

    market_data = get_daily_data(target_date)
    if not market_data or not _has_researchable_data(market_data):
        print("[ResearchReport] 无数据可生成研报")
        return {"date": target_date, "status": "no_data"}

    hotspot_discovery = get_preferred_analysis_result(target_date, ("hotspot_discovery",))

    payload = build_research_report(
        market_data=market_data,
        theme=theme,
        catalyst=catalyst,
        hotspot_discovery=hotspot_discovery,
    )
    save_analysis_result(target_date, "hot_theme_report", payload)

    report_path = _write_markdown_report(target_date, payload)
    print("[ResearchReport] 研报生成完成")
    return {
        "date": target_date,
        "status": "success",
        "theme": payload["theme"],
        "report_path": str(report_path),
    }


def build_research_report(
    market_data: dict,
    theme: str | None,
    catalyst: str | None,
    hotspot_discovery: dict | None = None,
) -> dict[str, Any]:
    """Build a compact, sample-based theme research report."""
    gainers = market_data.get("gainers", [])
    sectors = market_data.get("sectors", [])
    fund_flows = market_data.get("fund_flows", [])

    resolved_theme = _resolve_theme(theme, sectors, fund_flows, gainers, hotspot_discovery)
    evidence = get_curated_theme_evidence(resolved_theme)
    resolved_theme = normalize_theme_with_evidence_pack(resolved_theme, evidence)
    matched_sectors = _match_theme_sectors(resolved_theme, sectors)
    matched_fund_flows = _match_theme_fund_flows(resolved_theme, fund_flows)
    matched_gainers = _match_theme_gainers(resolved_theme, gainers)
    has_theme_matches = bool(matched_sectors or matched_fund_flows or matched_gainers)
    has_external_evidence = bool(evidence)
    if catalyst:
        resolved_catalyst = (
            catalyst if has_theme_matches else f"外部催化线索（待样本验证）：{catalyst}"
        )
    else:
        resolved_catalyst = (
            evidence["headline"]
            if has_external_evidence
            else f"近期围绕“{resolved_theme}”的市场关注度升温。"
            if resolved_theme and has_theme_matches
            else "当前暂无与该主题直接匹配的催化样本。"
        )

    chain_segments = _build_chain_segments(resolved_theme, matched_sectors, evidence)
    current_status = _build_current_status(
        resolved_theme, matched_gainers, matched_sectors, matched_fund_flows, evidence
    )
    next_milestones = _build_next_milestones(resolved_theme, resolved_catalyst, evidence)
    targets = _build_a_share_targets(resolved_theme, matched_gainers, matched_fund_flows, evidence)
    core_judgment = _build_core_judgment(resolved_theme, current_status, evidence)

    markdown = _render_markdown(
        theme=resolved_theme,
        catalyst=resolved_catalyst,
        core_judgment=core_judgment,
        chain_segments=chain_segments,
        current_status=current_status,
        next_milestones=next_milestones,
        targets=targets,
        evidence=evidence,
        source_tiers=evidence.get("source_tiers") if evidence else None,
    )

    return {
        "theme": resolved_theme,
        "catalyst": resolved_catalyst,
        "core_judgment": core_judgment,
        "chain_segments": chain_segments,
        "current_status": current_status,
        "next_milestones": next_milestones,
        "targets": targets,
        "evidence": evidence,
        "source_tiers": evidence.get("source_tiers") if evidence else None,
        "text": markdown,
    }


def _has_researchable_data(market_data: dict[str, Any]) -> bool:
    return any(market_data.get(key) for key in ("gainers", "sectors", "fund_flows"))


def _resolve_theme(
    theme: str | None,
    sectors: list[dict],
    fund_flows: list[dict],
    gainers: list[dict],
    hotspot_discovery: dict | None = None,
) -> str:
    if theme and theme.strip():
        return theme.strip()
    if hotspot_discovery and hotspot_discovery.get("lead_theme"):
        return str(hotspot_discovery["lead_theme"])
    if sectors:
        return str(sectors[0].get("name") or "市场热点主题")
    if fund_flows:
        return str(fund_flows[0].get("name") or "市场热点主题")
    if gainers:
        sector = get_stock_sector(gainers[0].get("name", ""))
        if sector and sector != "其他":
            return sector
    return "市场热点主题"


def _build_core_judgment(
    theme: str, current_status: list[str], evidence: dict[str, Any] | None
) -> str:
    if evidence:
        return f"围绕“{theme}”的公开资料催化已逐步增多，当前应把观察重点放在技术验证、设施建设和市场样本反馈是否同步增强。"
    if current_status and not all("尚未检出" in item for item in current_status):
        return f"当前围绕“{theme}”的观察重点，仍应放在样本强度、资金样本与后续催化验证上。"
    return f"当前围绕“{theme}”的样本线索有限，需继续等待更明确的验证信号。"


def _build_chain_segments(
    theme: str, sectors: list[dict], evidence: dict[str, Any] | None
) -> list[str]:
    if evidence:
        segments = [f"主题主线：围绕“{theme}”当前可重点跟踪的环节包括："]
        segments.extend([str(segment) for segment in evidence.get("segments", [])[:4]])
        if sectors:
            segment_names = [
                str(item.get("name") or "") for item in sectors[:3] if item.get("name")
            ]
            if segment_names:
                segments.append(f"当前板块样本中相对靠前的方向包括：{'、'.join(segment_names)}。")
    elif sectors:
        segments = [f"主题主线：围绕“{theme}”观察上游材料、核心部件、终端应用等环节。"]
        segment_names = [str(item.get("name") or "") for item in sectors[:3] if item.get("name")]
        if segment_names:
            segments.append(f"当前板块样本中相对靠前的方向包括：{'、'.join(segment_names)}。")
    else:
        segments = [f"当前样本中尚未检出与“{theme}”高度相关的板块样本。"]
    segments.append("第一版研报仅引用当前可核验样本，不扩展到未验证的细分产业链公司。")
    return segments


def _build_current_status(
    theme: str,
    gainers: list[dict],
    sectors: list[dict],
    fund_flows: list[dict],
    evidence: dict[str, Any] | None,
) -> list[str]:
    status: list[str] = []
    if evidence:
        for item in evidence.get("catalysts", [])[:2]:
            status.append(f"{item['date']}｜{item['source']}：{item['title']}。")
        for item in evidence.get("industry_context", [])[:2]:
            status.append(item)
    if sectors:
        top_sector = sectors[0]
        status.append(
            f"板块样本中，{top_sector['name']}当日涨幅{safe_float(top_sector.get('change_pct')):+.2f}%，位居当前样本前列。"
        )
    if gainers:
        top_gainer = gainers[0]
        status.append(
            f"个股样本中，{top_gainer['name']}涨幅{safe_float(top_gainer.get('change_pct')):+.2f}%，是当前最强样本之一。"
        )
    if fund_flows:
        top_flow = fund_flows[0]
        status.append(
            f"{fund_flow_scope_label(top_flow) or '资金'}样本中，{top_flow['name']}{fund_flow_direction_phrase(top_flow)}。"
        )
    if not status:
        status.append(f"当前样本中尚未检出与“{theme}”高度相关的市场反馈。")
    return status


def _build_next_milestones(
    theme: str, catalyst: str | None, evidence: dict[str, Any] | None
) -> list[str]:
    milestones = [f"继续跟踪“{theme}”是否有新的政策、订单、量产或事件催化出现。"]
    if evidence:
        milestones.extend(evidence.get("milestones", [])[:3])
    if catalyst:
        cleaned_catalyst = catalyst[:50].rstrip("。！？!?；;")
        if "暂无与该主题直接匹配的催化样本" in cleaned_catalyst:
            milestones.append("当前催化样本仍不足，先观察后续是否出现更直接的事件验证。")
        elif cleaned_catalyst.startswith("外部催化线索（待样本验证）："):
            external_hint = cleaned_catalyst.removeprefix("外部催化线索（待样本验证）：").strip()
            milestones.append(
                f"外部线索“{external_hint}”尚待样本验证，先观察后续市场反馈是否跟进。"
            )
        else:
            milestones.append(f"围绕当前催化：{cleaned_catalyst}，继续观察市场反馈是否扩散。")
    milestones.append("若板块样本与资金样本同步走强，再提高主题优先级。")
    return milestones


def _build_a_share_targets(
    theme: str, gainers: list[dict], fund_flows: list[dict], evidence: dict[str, Any] | None
) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()

    if evidence:
        for item in evidence.get("targets", []):
            if item["name"] in seen:
                continue
            candidates.append(
                {
                    "name": item["name"],
                    "code": item.get("code", ""),
                    "reason": f"公开资料线索：{item['reason']}（来源：{item['source']}）",
                    "tier": item.get("tier", ""),
                }
            )
            seen.add(item["name"])

    for item in gainers[:8]:
        name = str(item.get("name") or "")
        code = str(item.get("code") or "")
        if not name or name in seen:
            continue
        sector = get_stock_sector(name)
        if theme in sector or theme in name:
            candidates.append(
                {
                    "name": name,
                    "code": code,
                    "reason": f"强势股样本，所属方向：{sector}",
                }
            )
            seen.add(name)

    for item in fund_flows[:5]:
        leader = str(item.get("leader_stock") or "")
        if not leader or leader in seen:
            continue
        candidates.append(
            {
                "name": leader,
                "code": "",
                "reason": f"来自{fund_flow_scope_label(item) or '资金'}样本“{item.get('name', '')}”的领涨股。",
            }
        )
        seen.add(leader)

    return candidates[:8]


def _match_theme_sectors(theme: str, sectors: list[dict]) -> list[dict]:
    return [item for item in sectors if theme in str(item.get("name") or "")]


def _match_theme_fund_flows(theme: str, fund_flows: list[dict]) -> list[dict]:
    matched = []
    for item in fund_flows:
        if theme in str(item.get("name") or "") or theme in str(item.get("leader_stock") or ""):
            matched.append(item)
    return matched


def _match_theme_gainers(theme: str, gainers: list[dict]) -> list[dict]:
    matched = []
    for item in gainers:
        name = str(item.get("name") or "")
        sector = get_stock_sector(name)
        if theme in name or theme in sector:
            matched.append(item)
    return matched


def _render_markdown(
    theme: str,
    catalyst: str,
    core_judgment: str,
    chain_segments: list[str],
    current_status: list[str],
    next_milestones: list[str],
    targets: list[dict[str, str]],
    evidence: dict[str, Any] | None,
    source_tiers: dict[str, list[str]] | None,
) -> str:
    lines = [
        f"# {theme} 主题研究摘要",
        "",
        "## 核心催化",
        catalyst,
        "",
        "## 核心判断",
        core_judgment,
        "",
        "## 产业链 / 细分方向",
    ]
    lines.extend([f"- {item}" for item in chain_segments])
    lines.extend(["", "## 当前进展"])
    lines.extend([f"- {item}" for item in current_status])
    if evidence and evidence.get("catalysts"):
        lines.extend(["", "## 公开资料补充"])
        for item in evidence["catalysts"][:4]:
            tier = f"[{item['tier']}] " if item.get("tier") else ""
            lines.append(f"- {item['date']}｜{tier}{item['source']}：{item['summary']}")
    if source_tiers:
        lines.extend(["", "## 来源分级"])
        for tier, items in source_tiers.items():
            lines.append(f"- {tier}：{'；'.join(items)}")
    lines.extend(["", "## 下一阶段里程碑"])
    lines.extend([f"- {item}" for item in next_milestones])
    lines.extend(["", "## 代表性A股公司"])
    if targets:
        for item in targets:
            code_prefix = f"{item['code']} " if item.get("code") else ""
            tier_prefix = f"[{item['tier']}] " if item.get("tier") else ""
            lines.append(f"- {tier_prefix}{code_prefix}{item['name']}：{item['reason']}")
    else:
        lines.append("- 当前样本不足，暂未筛出高置信度代表性标的。")
    lines.extend(
        [
            "",
            "## 说明",
            "- 本摘要优先使用当前可核验样本与公开催化描述。",
            "- 若需形成正式研报，应继续补充公告、政策和产业数据验证。",
        ]
    )
    return "\n".join(lines)


def _write_markdown_report(date: str, payload: dict[str, Any]) -> Path:
    report_path = get_reports_dir_for_date(date) / f"{date}_hot_theme_research_report.md"
    report_path.write_text(payload["text"], encoding="utf-8")
    return report_path
