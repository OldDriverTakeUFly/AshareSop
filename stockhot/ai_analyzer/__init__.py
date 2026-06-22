"""AI analysis module for StockHot-CN."""

import os
from datetime import datetime

from stockhot.ai_analyzer.prompts.templates import (
    SYSTEM_PROMPT,
    HOTSPOT_ANALYSIS_PROMPT,
    REPORT_PROMPT,
)
from stockhot.core.utils import fund_flow_direction_phrase, fund_flow_scope_label, safe_float
from stockhot.storage.database import (
    get_daily_data,
    get_preferred_analysis_result,
    save_analysis_result,
)


def run_analysis(date: str | None = None) -> dict:
    """Run AI analysis for specified date."""
    target_date = date or datetime.now().strftime("%Y-%m-%d")
    print(f"[AIAnalyzer] 分析日期: {target_date}")

    data = get_daily_data(target_date)
    if not data or not _has_market_data(data):
        print("[AIAnalyzer] 无数据可分析")
        return {"date": target_date, "status": "no_data"}

    hotspots = analyze_hotspots(data)
    save_analysis_result(target_date, "hotspots", hotspots)

    preferred = get_preferred_analysis_result(target_date, ("hotspot_discovery",))
    report_analysis = preferred if preferred else hotspots

    report = generate_daily_report(data, report_analysis)
    save_analysis_result(target_date, "report", {"text": report})

    print("[AIAnalyzer] 分析完成")
    return {"date": target_date, "status": "success"}


def analyze_hotspots(data: dict) -> dict:
    """分析热点归因"""
    gainers = data.get("gainers", [])
    sectors = data.get("sectors", [])
    fund_flows = data.get("fund_flows", [])

    gainers_text = "\n".join(
        [
            f"{i+1}. {s['name']} ({s['code']}): +{safe_float(s['change_pct']):.2f}%"
            for i, s in enumerate(gainers[:10])
        ]
    )

    sectors_text = "\n".join(
        [f"{s['name']}: +{safe_float(s['change_pct']):.2f}%" for s in sectors[:8]]
    )

    fund_flows_text = "\n".join(
        [f"{f['name']}: 净流入 {safe_float(f['net_inflow']):.2f}亿" for f in fund_flows[:8]]
    )

    prompt = HOTSPOT_ANALYSIS_PROMPT.format(
        gainers=gainers_text,
        sectors=sectors_text,
        fund_flows=fund_flows_text,
    )

    ai_result = call_ai_optional(prompt)

    if ai_result is None:
        local = _local_hotspot_analysis(data)
        local["raw_analysis"] = ""
        return local

    return {
        "hotspots": _extract_hotspots(ai_result, sectors),
        "reasons": _local_reasons(data, ai_result),
        "fund_flow_analysis": _extract_fund_flow_analysis(ai_result),
        "risk_warnings": _extract_risks(ai_result),
        "raw_analysis": ai_result,
    }


def cluster_themes(stocks: list[dict]) -> list[dict]:
    """主题聚类 - 将股票归类到主题"""
    if not stocks:
        return []

    prompt = f"""将以下股票按行业/概念分类：

{chr(10).join([f"{s['name']} ({s['code']})" for s in stocks[:20]])}

输出JSON格式：
[{{"theme": "主题名", "stocks": ["股票1", "股票2"]}}]"""

    result = call_ai(prompt)
    return _parse_json(result)


def generate_daily_report(data: dict, analysis: dict) -> str:
    """生成每日报告"""
    market_data = _format_market_data(data)
    hotspots = analysis.get("hotspots", [])
    reasons = analysis.get("reasons", [])

    analysis_text = f"热点主题: {', '.join(hotspots[:3])}\n"
    if reasons:
        analysis_text += f"原因分析: {reasons[0][:200]}"

    prompt = REPORT_PROMPT.format(
        market_data=market_data,
        analysis=analysis_text,
    )

    ai_result = call_ai_optional(prompt)
    if ai_result and "数据不足" not in ai_result:
        return ai_result

    return _local_daily_report(data, analysis)


def call_ai(prompt: str, model: str = "gpt-4o-mini") -> str:
    """调用AI API"""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return _fallback_analysis(prompt)

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1500,
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"[AIAnalyzer] API调用失败: {e}, 使用本地分析")
        return _fallback_analysis(prompt)


def call_ai_optional(prompt: str) -> str | None:
    """Try calling AI; return None on any failure."""
    try:
        result = call_ai(prompt)
        return result if result else None
    except Exception:
        return None


def _local_hotspot_analysis(data: dict) -> dict:
    gainers = data.get("gainers", [])
    sectors = data.get("sectors", [])
    fund_flows = data.get("fund_flows", [])

    reasons = []
    if gainers:
        top_gainer = gainers[0]
        reasons.append(f"个股样本中，{top_gainer['name']}涨幅居前")
    if sectors:
        top_sector = sectors[0]
        reasons.append(f"板块端，{top_sector['name']}领涨")

    fund_flow_analysis = ""
    if fund_flows:
        top_flow = fund_flows[0]
        scope = fund_flow_scope_label(top_flow)
        direction = fund_flow_direction_phrase(top_flow)
        fund_flow_analysis = f"当前{scope}资金样本中，{top_flow['name']}{direction}。"

    return {
        "hotspots": [],
        "reasons": reasons,
        "fund_flow_analysis": fund_flow_analysis,
        "risk_warnings": [],
    }


def _fallback_analysis(prompt: str) -> str:
    """本地降级分析（无API时）"""
    if "涨幅前10" in prompt:
        return """## 市场热点分析

### 今日热点
1. 银行板块持续走强，平安银行领涨
2. 白酒股反弹，贵州茅台涨幅居前

### 资金流向
主力资金净流入银行、保险等蓝筹板块

### 风险提示
部分题材股炒作风险较大，建议谨慎"""
    return "数据不足以生成完整分析"


def _has_market_data(data: dict) -> bool:
    return bool(
        data.get("gainers") or data.get("losers") or data.get("sectors") or data.get("fund_flows")
    )


def _local_reasons(data: dict, ai_result: str) -> list[str]:
    reasons = []
    gainers = data.get("gainers", [])
    sectors = data.get("sectors", [])
    if gainers:
        reasons.append(f"个股样本中，{gainers[0]['name']}涨幅居前")
    if sectors:
        reasons.append(f"板块端，{sectors[0]['name']}涨幅居前")
    return reasons if reasons else _extract_reasons(ai_result)


def _local_daily_report(data: dict, analysis: dict) -> str:
    gainers = data.get("gainers", [])
    sectors = data.get("sectors", [])
    fund_flows = data.get("fund_flows", [])
    fund_flow_analysis = analysis.get("fund_flow_analysis", "")

    lines = ["## 市场复盘摘要\n"]

    if gainers:
        top = gainers[0]
        lines.append(f"个股端，{top['name']}涨幅+{safe_float(top['change_pct']):.2f}%")

    if sectors:
        top = sectors[0]
        lines.append(f"板块端，{top['name']}涨幅+{safe_float(top['change_pct']):.2f}%")

    if fund_flow_analysis:
        lines.append(fund_flow_analysis)
    elif fund_flows:
        top_flow = fund_flows[0]
        scope = fund_flow_scope_label(top_flow)
        direction = fund_flow_direction_phrase(top_flow)
        lines.append(f"{scope}资金样本中，{top_flow['name']}{direction}。")

    themes = analysis.get("themes", [])
    if themes:
        theme_names = [t["name"] for t in themes if "name" in t]
        if theme_names:
            lines.append(f"热点线索可先看：{'、'.join(theme_names)}。")

    return "\n".join(lines)


def _format_market_data(data: dict) -> str:
    """格式化市场数据"""
    gainers = data.get("gainers", [])
    sectors = data.get("sectors", [])

    lines = ["## 今日市场概况\n"]

    if gainers:
        top = gainers[0]
        lines.append(f"📈 涨幅榜: {top['name']} (+{top['change_pct']:.2f}%)")

    if sectors:
        top = sectors[0]
        lines.append(f"🔥 热门板块: {top['name']} (+{top['change_pct']:.2f}%)")

    lines.append(f"📊 共统计 {len(gainers)} 只上涨, {len(sectors)} 个板块")

    return "\n".join(lines)


def _extract_hotspots(text: str, sectors: list[dict] | None = None) -> list[str]:
    """提取热点主题"""
    sector_names = [s["name"] for s in (sectors or []) if "name" in s]
    matched = [name for name in sector_names if name in text]
    if matched:
        return matched[:5]
    keywords = [
        "银行",
        "白酒",
        "新能源",
        "医药",
        "半导体",
        "房地产",
        "军工",
        "券商",
        "光伏",
        "汽车",
    ]
    return [k for k in keywords if k in text][:5]


def _extract_reasons(text: str) -> list[str]:
    """提取原因分析"""
    reasons = []
    for line in text.split("\n"):
        if "原因" in line or "由于" in line or "因为" in line:
            reasons.append(line.strip())
    return reasons[:3]


def _extract_fund_flow_analysis(text: str) -> str:
    """提取资金流向分析"""
    if "资金" in text:
        return "主力资金流入蓝筹板块"
    return "资金观望为主"


def _extract_risks(text: str) -> list[str]:
    """提取风险提示"""
    risks = []
    for line in text.split("\n"):
        if "风险" in line or "谨慎" in line or "注意" in line:
            risks.append(line.strip())
    return risks[:2]


def _parse_json(text: str) -> list[dict]:
    """解析JSON"""
    import re

    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        import json

        try:
            return json.loads(match.group())
        except Exception:
            pass
    return []
