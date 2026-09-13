#!/usr/bin/env python3
"""全市场前瞻解禁日历 v2(2026-09-13)——锁定规则推导版。

v1 教训:share_float 只收「已公告」记录,首发 12/36 个月解禁公告往往临近才挂,
远期事件在 API 里天然缺失(159 只 cohort 全空验证)。同花顺图的真实做法是按锁定规则推导,
本脚本照做:

- 事件推导:解禁日 = 上市日 + 12 个月(非控股/创投批)或 + 36 个月(控股批),
  仅保留落在未来 13 个月的事件;+6 个月战配批占比小,不计(局限)。
- 占比口径:上限估算 = (总股本-流通股本)/总股本(daily_basic 最新)。
  对未到 12mo 的次新,该值含 36mo 控股部分 → 12mo 事件占比被高估(上界);
  对已过 12mo、待 36mo 的标的,该值即 36mo 待解禁部分 → 基本精确。
  已公告事件(share_float,含精确占比/股数)存在时优先覆盖估算。
- 金额 = 待解禁股数 × 最新收盘(上界同占比口径)。

产出:storage/files/unlock/forward_unlock.csv + 月度/板块/Top40 统计(stdout)。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "storage" / "files" / "unlock"
ANN_CACHE = REPO / "storage" / "database" / "unlock_market_cache.json"


def _pro():
    from dotenv import load_dotenv

    load_dotenv(str(REPO / ".env"))
    import os

    import tushare as ts

    return ts.pro_api(os.environ["TUSHARE_TOKEN"])


def _plus_months(d: datetime, m: int) -> datetime:
    y, mo = d.year + (d.month - 1 + m) // 12, (d.month - 1 + m) % 12 + 1
    day = min(d.day, [31, 29 if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mo - 1])
    return datetime(y, mo, day)


def main() -> None:
    pro = _pro()
    today = datetime.now()
    horizon = today + timedelta(days=395)
    today_s, horizon_s = today.strftime("%Y%m%d"), horizon.strftime("%Y%m%d")

    basic = pro.stock_basic(list_status="L", fields="ts_code,name,industry,list_date")
    basic = basic[basic.list_date >= (today - timedelta(days=49 * 31)).strftime("%Y%m%d")]

    # 最新交易日收盘+股本(daily_basic 不支持多码批量,逐票拉;~700 只≈2 分钟)
    # 最近开市日(周日查 pre_trade_date 会拿 nan,回溯 15 天取 is_open=1 的最大日)
    tc = pro.trade_cal(exchange="SSE",
                       start_date=(today - timedelta(days=15)).strftime("%Y%m%d"),
                       end_date=today_s, fields="cal_date,is_open")
    last_td = str(tc[tc.is_open == 1].cal_date.max())
    import time as _t

    px, total_sh, float_sh = {}, {}, {}
    codes = basic.ts_code.tolist()
    for i, code in enumerate(codes):
        try:
            r = pro.daily_basic(ts_code=code, trade_date=last_td,
                                fields="close,total_share,float_share")
            if len(r):
                px[code] = float(r.iloc[0].close)
                total_sh[code] = float(r.iloc[0].total_share)
                float_sh[code] = float(r.iloc[0].float_share)
        except Exception:
            pass
        if i % 100 == 0:
            _t.sleep(0.2)
        _t.sleep(0.07)
    print(f"股本/价格覆盖 {len(px)}/{len(codes)} 只")

    # 已公告事件缓存(v1 拉过 159 只,复用)
    ann: dict[str, list[dict]] = {}
    if ANN_CACHE.exists():
        try:
            ann = json.loads(ANN_CACHE.read_text()).get("events", {})
        except Exception:
            pass

    recs = []
    for row in basic.itertuples():
        if row.ts_code not in px:
            continue
        ld = datetime.strptime(row.list_date, "%Y%m%d")
        ts, fs = total_sh[row.ts_code], float_sh[row.ts_code]
        if ts <= 0:
            continue
        locked_pct = max(0.0, (ts - fs) / ts * 100)

        # 已公告的未来事件(精确)优先
        # 注意:share_float.float_share 单位是「股」,统一转万股
        announced = [e for e in ann.get(row.ts_code, [])
                     if today_s <= e["date"] <= horizon_s]
        if announced:
            for e in announced:
                share_wan = e["share_wan"] / 1e4
                recs.append({"ts_code": row.ts_code, "name": row.name,
                             "industry": row.industry, "float_date": e["date"],
                             "ratio": e["ratio"],
                             "share_wan": round(share_wan, 1),
                             "amount_yi": round(share_wan * px[row.ts_code] / 1e4, 2),
                             "src": "已公告"})
            continue

        # 推导:12mo 批(上市 12~14 个月内未解禁的)与 36mo 批
        for months, tag in ((12, "推导12mo"), (36, "推导36mo")):
            fd = _plus_months(ld, months)
            if not (today <= fd <= horizon):
                continue
            if months == 12 and locked_pct < 8:
                continue  # 12mo 批已解禁完毕(次新解禁过),别把 36mo 剩余错记成 12mo
            share_wan = ts - fs
            recs.append({"ts_code": row.ts_code, "name": row.name,
                         "industry": row.industry, "float_date": fd.strftime("%Y%m%d"),
                         "ratio": round(locked_pct, 2), "share_wan": round(share_wan, 1),
                         "amount_yi": round(share_wan * px[row.ts_code] / 1e4, 2),
                         "src": tag})

    df = pd.DataFrame(recs)
    if df.empty:
        print("无事件"); return
    df = df[df.ratio >= 15].sort_values("float_date").reset_index(drop=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "forward_unlock.csv"
    df.to_csv(out, index=False)

    big = df[df.amount_yi >= 2]
    print(f"事件 {len(df)} 个(占比≥15%),金额≥2亿 {len(big)} 个(口径:上限估算/已公告优先)")
    m = big.assign(month=big.float_date.str[:6]).groupby("month").agg(
        n=("ts_code", "count"), amount=("amount_yi", "sum")).round(0)
    print("\n== 月度分布(金额≥2亿,亿元为上限估算)=="); print(m.to_string())
    h1 = big[big.float_date <= "20270331"]
    print("\n== 未来 6 个月板块 Top12(金额≥2亿)==")
    print(h1.groupby("industry").agg(n=("ts_code", "count"), amount=("amount_yi", "sum"))
          .sort_values("amount", ascending=False).head(12).round(0).to_string())
    print("\n== Top40(金额降序)==")
    print(big.sort_values("amount_yi", ascending=False).head(40)
          [["float_date", "name", "amount_yi", "ratio", "industry", "src"]].to_string(index=False))
    print(f"\nCSV: {out}")


if __name__ == "__main__":
    main()
