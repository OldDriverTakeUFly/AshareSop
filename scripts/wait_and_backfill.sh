#!/bin/bash
# 监控 A/B 实验，完成后自动执行 daily_basic PE 回填
#
# 用法：
#   cd /home/leo/Projects/CodeAgentDashboard
#   nohup bash scripts/wait_and_backfill.sh > logs/wait_and_backfill.log 2>&1 &

set -e
cd /home/leo/Projects/CodeAgentDashboard
export PROJECT_ROOT=/home/leo/Projects/CodeAgentDashboard
export PYTHONPATH=$PROJECT_ROOT

LOG_PREFIX="[wait_and_backfill]"

echo "$LOG_PREFIX 启动监控 $(date)"
echo "$LOG_PREFIX 等待以下实验完成:"
echo "$LOG_PREFIX   - 量能比防御 (volratio_abx)"
echo "$LOG_PREFIX   - 吃波段 (trail_abx)"
echo ""

# 等待两个 A/B 进程完成
WAIT_PIDS=""
while true; do
    RUNNING=""
    for pattern in "volratio_abx" "trail_abx"; do
        PID=$(pgrep -f "$pattern" 2>/dev/null || true)
        if [ -n "$PID" ]; then
            RUNNING="$RUNNING $pattern(PID=$PID)"
        fi
    done

    if [ -z "$RUNNING" ]; then
        echo "$LOG_PREFIX 所有 A/B 实验已完成 $(date)"
        break
    fi

    echo "$LOG_PREFIX 仍在运行:$RUNNING ... 等待 300s"
    sleep 300
done

# ── 输出 A/B 结果摘要 ──
echo ""
echo "$LOG_PREFIX ═════════════════════════════════════════"
echo "$LOG_PREFIX A/B 实验结果摘要"
echo "$LOG_PREFIX ═════════════════════════════════════════"

for f in volratio_abx trail_abx; do
    JSON="logs/abx/${f}.json"
    if [ -f "$JSON" ]; then
        echo ""
        echo "$LOG_PREFIX --- $f ---"
        PYTHONPATH=$PROJECT_ROOT .venv/bin/python -c "
import json
with open('$JSON') as fp: d = json.load(fp)
for r in sorted(d['results'], key=lambda x: -x.get('sharpe_real', -99)):
    print(f'  {r[\"label\"]:<20} ret={r.get(\"return_pct\",0):+.2f}%  MDD={r.get(\"max_drawdown_pct\",0):.1f}%  Sharpe={r.get(\"sharpe_real\",0):+.3f}')
" 2>/dev/null || echo "  (结果解析失败)"
    else
        echo "$LOG_PREFIX --- $f: 结果文件不存在 ---"
    fi
done

# ── 执行 daily_basic PE 回填 ──
echo ""
echo "$LOG_PREFIX ═════════════════════════════════════════"
echo "$LOG_PREFIX 开始 daily_basic PE/PB 回填"
echo "$LOG_PREFIX ═════════════════════════════════════════"
echo "$LOG_PREFIX 预计耗时: ~7 分钟"

PYTHONPATH=$PROJECT_ROOT .venv/bin/python scripts/backfill/backfill_macro_data.py \
    --only daily_basic --start 20210101 --end 20260731

echo ""
echo "$LOG_PREFIX ═════════════════════════════════════════"
echo "$LOG_PREFIX daily_basic 回填完成 $(date)"
echo "$LOG_PREFIX ═════════════════════════════════════════"

# ── 验证回填结果 ──
PYTHONPATH=$PROJECT_ROOT .venv/bin/python -c "
from stockhot.data_layer.market_db import get_connection
with get_connection() as c:
    for y in [2021,2022,2023,2024,2025,2026]:
        row = c.execute(f\"SELECT COUNT(*) FROM daily_basic WHERE trade_date >= '{y}0101' AND trade_date <= '{y}1231' AND pe_ttm > 0\").fetchone()
        print(f'  {y}: {row[0]:>10,} rows with pe_ttm')
"

echo ""
echo "$LOG_PREFIX 全部完成 $(date)"
