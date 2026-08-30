# -*- coding: utf-8 -*-
"""AI国产替代产业链研报——标的池估值快照（截面 + YTD 涨幅）。

用途: docs/产业链研报/AI建设国产替代产业链深度研报.md 第八章标的表数据源。
输出: JSON 至 stdout,含 每只标的 PE_TTM/PB/PS/总市值/YTD涨幅/最新交易日。
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd

from davis_analyzer.tushare_client import TushareClient

# ── 标的池:环节 → [(ts_code, name)] ──
POOLS: dict[str, list[tuple[str, str]]] = {
    "算力芯片设计": [
        ("688256.SH", "寒武纪"),
        ("688041.SH", "海光信息"),
        ("688047.SH", "龙芯中科"),
    ],
    "晶圆制造": [
        ("688981.SH", "中芯国际"),
        ("688396.SH", "华润微"),
    ],
    "存储/HBM链": [
        ("603986.SH", "兆易创新"),
        ("688525.SH", "佰维存储"),
        ("301308.SZ", "江波龙"),
        ("300475.SZ", "香农芯创"),
        ("000021.SZ", "深科技"),
    ],
    "先进封装/封测": [
        ("600584.SH", "长电科技"),
        ("002156.SZ", "通富微电"),
        ("002185.SZ", "华天科技"),
    ],
    "半导体设备": [
        ("002371.SZ", "北方华创"),
        ("688012.SH", "中微公司"),
        ("688072.SH", "拓荆科技"),
        ("688120.SH", "华海清科"),
        ("688082.SH", "盛美上海"),
        ("688037.SH", "芯源微"),
        ("688361.SH", "中科飞测"),
        ("300604.SZ", "长川科技"),
    ],
    "半导体材料": [
        ("688126.SH", "沪硅产业"),
        ("002409.SZ", "雅克科技"),
        ("300346.SZ", "南大光电"),
        ("688019.SH", "安集科技"),
        ("300054.SZ", "鼎龙股份"),
        ("300666.SZ", "江丰电子"),
        ("603650.SH", "彤程新材"),
    ],
    "覆铜板/PCB": [
        ("600183.SH", "生益科技"),
        ("002916.SZ", "深南电路"),
        ("002463.SZ", "沪电股份"),
        ("300476.SZ", "胜宏科技"),
        ("300395.SZ", "菲利华"),
    ],
    "EDA": [
        ("301269.SZ", "华大九天"),
        ("688206.SH", "概伦电子"),
    ],
    "光模块/光芯片": [
        ("300308.SZ", "中际旭创"),
        ("300502.SZ", "新易盛"),
        ("300394.SZ", "天孚通信"),
        ("688498.SH", "源杰科技"),
        ("688313.SH", "仕佳光子"),
        ("688048.SH", "长光华芯"),
    ],
    "交换机/网络": [
        ("688702.SH", "盛科通信"),
        ("000938.SZ", "紫光股份"),
        ("301165.SZ", "锐捷网络"),
    ],
    "AI服务器": [
        ("000977.SZ", "浪潮信息"),
        ("601138.SH", "工业富联"),
        ("000063.SZ", "中兴通讯"),
    ],
    "液冷/电源": [
        ("002837.SZ", "英维克"),
        ("301018.SZ", "申菱环境"),
        ("002851.SZ", "麦格米特"),
        ("300870.SZ", "欧陆通"),
    ],
}


def main() -> None:
    client = TushareClient()
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=300)).strftime("%Y%m%d")

    rows: list[dict[str, object]] = []
    for seg, stocks in POOLS.items():
        for ts_code, name in stocks:
            try:
                db = client.get_daily_basic(ts_code, start, end)
                px = client.get_daily_prices(ts_code, start, end)
            except Exception as exc:  # noqa: BLE001
                rows.append({"segment": seg, "ts_code": ts_code, "name": name, "error": str(exc)})
                continue
            if db is None or db.empty or px is None or px.empty:
                rows.append({"segment": seg, "ts_code": ts_code, "name": name, "error": "no data"})
                continue

            db = db.sort_values("trade_date")
            px = px.sort_values("trade_date")
            last = db.iloc[-1]

            pe = pd.to_numeric(db["pe_ttm"], errors="coerce").dropna()
            pb = pd.to_numeric(db["pb"], errors="coerce").dropna()
            ps = pd.to_numeric(db["ps"], errors="coerce").dropna()

            close = pd.to_numeric(px["close"], errors="coerce").dropna()
            # YTD: 2025 年最后一个收盘 vs 最新收盘
            y2025 = px[px["trade_date"] <= "20251231"]
            base = pd.to_numeric(y2025["close"], errors="coerce").dropna()
            ytd = None
            if not base.empty and not close.empty and base.iloc[-1] > 0:
                ytd = round((close.iloc[-1] / base.iloc[-1] - 1) * 100, 1)

            def _pct(series: pd.Series) -> float | None:
                if series.empty:
                    return None
                cur = series.iloc[-1]
                return round((series < cur).sum() / len(series) * 100, 1)

            rows.append(
                {
                    "segment": seg,
                    "ts_code": ts_code,
                    "name": name,
                    "trade_date": last["trade_date"],
                    "close": round(float(close.iloc[-1]), 2) if not close.empty else None,
                    "pe_ttm": round(float(pe.iloc[-1]), 1) if not pe.empty else None,
                    "pe_pct_3y": _pct(pe),
                    "pb": round(float(pb.iloc[-1]), 2) if not pb.empty else None,
                    "pb_pct_3y": _pct(pb),
                    "ps": round(float(ps.iloc[-1]), 2) if not ps.empty else None,
                    "ps_pct_3y": _pct(ps),
                    "total_mv_yi": round(float(last["total_mv"]) / 1e4, 1),  # 万元→亿元
                    "ytd_pct": ytd,
                }
            )
            print(f"ok {ts_code} {name}", flush=True)

    print(json.dumps(rows, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
