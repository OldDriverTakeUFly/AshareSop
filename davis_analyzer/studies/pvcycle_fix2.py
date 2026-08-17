# ── 光伏取数修复2:None 哨兵(pe_ttm 亏损股返回 None 而非 NaN) ──
import os, sys, json, time
os.environ.setdefault("PROJECT_ROOT", "/home/leo/Projects/CodeAgentDashboard")
sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")
from dotenv import load_dotenv
load_dotenv('/home/leo/Projects/CodeAgentDashboard/.env', override=True)

import pandas as pd
from stockhot.tushare_config import get_pro_api
pro = get_pro_api()

PATH = "/home/leo/Projects/CodeAgentDashboard/davis_analyzer/studies/pvcycle_data.json"
with open(PATH, encoding="utf-8") as f:
    out = json.load(f)

TARGETS = {
    "600438.SH": "通威股份", "601012.SH": "隆基绿能", "002129.SZ": "TCL中环",
    "002459.SZ": "晶澳科技", "688223.SH": "晶科能源", "688599.SH": "天合光能",
    "688303.SH": "大全能源", "688472.SH": "阿特斯",
}

def ok(x):
    """None 与 NaN 均视为无效"""
    return x is not None and x == x

def num(x, nd=2):
    return round(float(x), nd) if ok(x) else None

def pct_rank(series, cur):
    s = [x for x in series if ok(x)]
    if not s or not ok(cur):
        return None
    return round(100.0 * sum(1 for x in s if x <= cur) / len(s), 1)

def fetch_db_segments(code, start, end):
    from datetime import datetime, timedelta
    frames = []
    d0 = datetime.strptime(start, "%Y%m%d"); d1 = datetime.strptime(end, "%Y%m%d")
    while d0 <= d1:
        d2 = min(d0 + timedelta(days=499), d1)
        df = pro.daily_basic(ts_code=code, start_date=d0.strftime("%Y%m%d"), end_date=d2.strftime("%Y%m%d"),
                             fields="trade_date,close,total_mv,pb,pe_ttm,ps_ttm")
        if df is not None and len(df) > 0:
            frames.append(df)
        time.sleep(0.18)
        d0 = d2 + timedelta(days=1)
    if not frames: return None
    all_df = pd.concat(frames).drop_duplicates(subset="trade_date").reset_index(drop=True)
    return all_df.sort_values("trade_date").reset_index(drop=True)

for code, name in TARGETS.items():
    try:
        df = fetch_db_segments(code, "20230814", "20260814")
        if df is None or len(df) < 700:
            out[code]["snapshot_err"] = f"insufficient rows: {0 if df is None else len(df)}"
            continue
        idx = len(df) - 1
        while idx >= 0 and not (ok(df.iloc[idx]["close"]) and ok(df.iloc[idx]["pb"])):
            idx -= 1
        last = df.iloc[idx]
        out[code]["snapshot"] = {
            "trade_date": str(last["trade_date"]),
            "close": num(last["close"]),
            "total_mv_yi": num(last["total_mv"], 1) and round(float(last["total_mv"]) / 1e4, 1) if ok(last["total_mv"]) else None,
            "pb": num(last["pb"]),
            "pb_pct": pct_rank(df["pb"].tolist(), last["pb"]),
            "pe_ttm": num(last["pe_ttm"], 1),
            "pe_pct": pct_rank(df["pe_ttm"].tolist(), last["pe_ttm"]),
            "ps_ttm": num(last["ps_ttm"]),
            "ps_pct": pct_rank(df["ps_ttm"].tolist(), last["ps_ttm"]),
            "n_days": int(len(df)),
        }
        out[code].pop("snapshot_err", None)
        s = out[code]["snapshot"]
        print(f"ok {code} {name} {s['trade_date']} pb={s['pb']}({s['pb_pct']}%) mv={s['total_mv_yi']} pe={s['pe_ttm']} ps={s['ps_ttm']}({s['ps_pct']}%) n={s['n_days']}", file=sys.stderr)
    except Exception as e:
        out[code]["snapshot_err"] = str(e)
        print(f"fail {code}: {e}", file=sys.stderr)
    time.sleep(0.2)

with open(PATH, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print("saved", file=sys.stderr)
