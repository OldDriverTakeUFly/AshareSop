"""细分行业分类模块.

基于东财 industry（110 个分类）+ 关键词/手动映射，构建更细的行业分类。
解决"半导体"太粗（195 只）的问题，细分为半导体设备/存储/模拟芯片等。

分类来源：
1. 东财 industry（stock_basic 已有）—— 基础层
2. 关键词匹配 —— 自动细分层
3. 手动映射表 —— 精确细分层（用户维护）

用法：
    from davis_analyzer.sub_industry import get_sub_industry
    sub = get_sub_industry("002371.SZ")  # → "半导体设备"
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from loguru import logger

_CONFIG_DIR = os.path.join(os.path.dirname(__file__), "config")
_MAP_FILE = os.path.join(_CONFIG_DIR, "sub_industry_map.json")


# ── 关键词自动匹配规则 ──
# 对东财"半导体"(195 只)等大行业，用公司名/业务关键词进一步细分
_KEYWORD_RULES: dict[str, list[tuple[str, str]]] = {
    "半导体": [
        # (关键词列表, 细分行业名)
        (["设备", "刻蚀", "薄膜沉积", "清洗", "测试设备"], "半导体设备"),
        (["存储", "DRAM", "NAND", "闪存"], "存储芯片"),
        (["模拟", "ADC", "DAC", "电源管理", "射频"], "模拟芯片"),
        (["封测", "封装", "测试"], "半导体封测"),
        (["制造", "代工", "中芯"], "集成电路制造"),
        (["材料", "硅片", "光刻胶", "靶材", "气体"], "半导体材料"),
        (["设计", "SoC", "MCU", "AI芯片", "GPU"], "数字芯片设计"),
        (["功率", "IGBT", "SiC", "MOSFET", "二极管"], "功率半导体"),
    ],
    "元器件": [
        (["PCB", "印制电路", "覆铜板"], "PCB"),
        (["电容", "电阻", "电感", "被动"], "被动元件"),
        (["连接器", "接插件"], "连接器"),
    ],
    "通信设备": [
        (["光通信", "光模块", "光纤"], "光通信"),
        (["交换机", "路由器", "基站"], "网络设备"),
        (["终端", "手机", "智能终端"], "通信终端"),
    ],
}


@lru_cache(maxsize=1)
def _load_manual_map() -> dict[str, str]:
    """Load manual sub-industry mapping (ts_code → sub_industry)."""
    if not os.path.exists(_MAP_FILE):
        return {}
    with open(_MAP_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Flatten: {"半导体设备": ["002371.SZ", ...], ...} → {"002371.SZ": "半导体设备", ...}
    flat: dict[str, str] = {}
    for sub_name, codes in data.items():
        if sub_name.startswith("_"):
            continue  # skip metadata keys like "_说明"
        if not isinstance(codes, list):
            continue
        for code in codes:
            flat[code] = sub_name
    return flat


def get_sub_industry(ts_code: str, name: str = "", industry: str = "") -> str:
    """Get sub-industry classification for a stock.

    Priority:
    1. Manual mapping (sub_industry_map.json) — highest priority
    2. Keyword matching on company name — automatic
    3. Fallback to base industry (东财)

    Args:
        ts_code: stock code, e.g. "002371.SZ"
        name: company name (for keyword matching), e.g. "北方华创"
        industry: base industry (东财), e.g. "半导体"

    Returns:
        Sub-industry name, e.g. "半导体设备"
    """
    # 1. Manual mapping (highest priority)
    manual = _load_manual_map()
    if ts_code in manual:
        return manual[ts_code]

    # 2. Keyword matching
    if industry in _KEYWORD_RULES:
        for keywords, sub_name in _KEYWORD_RULES[industry]:
            if any(kw in name for kw in keywords):
                return sub_name

    # 3. Fallback to base industry
    return industry


def build_sub_industry_map(all_stocks: list[dict]) -> dict[str, str]:
    """Build ts_code → sub_industry mapping for all stocks.

    Args:
        all_stocks: list of {"ts_code": str, "name": str, "industry": str}

    Returns:
        {ts_code: sub_industry}
    """
    result: dict[str, str] = {}
    for stock in all_stocks:
        ts_code = stock.get("ts_code", "")
        name = stock.get("name", "")
        industry = stock.get("industry", "")
        if ts_code:
            result[ts_code] = get_sub_industry(ts_code, name, industry)
    return result


def get_all_sub_industries() -> list[str]:
    """Get all possible sub-industry names."""
    subs = set()
    for rules in _KEYWORD_RULES.values():
        for _, sub_name in rules:
            subs.add(sub_name)
    manual = _load_manual_map()
    subs.update(manual.values())
    return sorted(subs)
