"""Smoke test for _compute_volume_signals on known stocks."""
import os, sys
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)
from loguru import logger; logger.remove(); logger.add(sys.stderr, level="WARNING")

from davis_analyzer.paper_trading.executor import _compute_volume_signals

# Test on a mix of well-known A-share stocks across different sectors
TEST_CODES = [
    "000001.SZ",  # 平安银行 — 大金融
    "600519.SH",  # 贵州茅台 — 白酒
    "300750.SZ",  # 宁德时代 — 新能源
    "002475.SZ",  # 立讯精密 — 消费电子
    "688981.SH",  # 中芯国际 — 半导体
    "601318.SH",  # 中国平安 — 保险
    "000858.SZ",  # 五粮液 — 白酒
    "002371.SZ",  # 北方华创 — 半导体设备
]

# Test multiple recent dates to catch different market regimes
TEST_DATES = ["20260123", "20260321", "20260520", "20260715"]

for d in TEST_DATES:
    print(f"\n{'='*78}")
    print(f"  Date: {d}")
    print(f"{'='*78}")
    print(f"  {'ts_code':<12} {'signal':<20} {'score':>6} {'vol_ratio':>10} {'pos_pct':>8} {'box_amp':>8}")
    print(f"  {'-'*12} {'-'*20} {'-'*6} {'-'*10} {'-'*8} {'-'*8}")
    result = _compute_volume_signals(TEST_CODES, d)
    for code in TEST_CODES:
        r = result.get(code)
        if r is None:
            print(f"  {code:<12} (no data)")
            continue
        print(f"  {code:<12} {r['signal_type']:<20} {r['score']:>6.1f} "
              f"{r['vol_ratio']:>10.2f} {r['position_pct']:>7.1f}% {r['box_amplitude']:>7.1f}%")
