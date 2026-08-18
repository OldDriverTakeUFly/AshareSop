#!/usr/bin/env bash
# SessionStart: 注入项目运行状态上下文（省去 agent 每次开头的探索步骤）
# 容错设计：任何子命令失败不阻断，只跳过该项
cd "${CLAUDE_PROJECT_DIR:-${ZCODE_PROJECT_DIR:-$(dirname "$0")/../..}}" 2>/dev/null || true

CONTEXT="📋 项目快照 [$(date '+%Y-%m-%d %H:%M')]"

# 1. cron 运行状态（本会话多次因 cron 重复/丢失 debug）
CRON_COUNT=$(crontab -l 2>/dev/null | grep -c "^[0-9]" 2>/dev/null || echo "0")
INTRADAY_RUNNING=$(pgrep -f intraday_manager 2>/dev/null | wc -l || echo "0")
CONTEXT+=$'\n'"• cron 条目: ${CRON_COUNT} | 盘中监控进程: ${INTRADAY_RUNNING} 个"

# 2. git 未提交变更
DIRTY=$(git status --porcelain 2>/dev/null | wc -l || echo "0")
if [ "${DIRTY:-0}" -gt 0 ] 2>/dev/null; then
  CONTEXT+=$'\n'"• ⚠️ 有 ${DIRTY} 个未提交文件"
fi

# 3. 今日模拟交易笔数（用 python 替代 sqlite3 CLI——系统可能未装）
TODAY=$(date +%Y%m%d)
TRADES=$(python3 -c "
import sqlite3
try:
    conn = sqlite3.connect('storage/database/stockhot.db')
    print(conn.execute(\"SELECT COUNT(*) FROM paper_trades WHERE trade_date='$TODAY'\").fetchone()[0])
except: print('?')
" 2>/dev/null || echo "?")
CONTEXT+=$'\n'"• 今日模拟交易: ${TRADES} 笔"

CONTEXT+=$'\n'"• 数据库: market_data.db（行情/波动率/板块）+ stockhot.db（持仓/交易/资金）"

# 用 jq 包装 JSON 输出
if command -v jq >/dev/null 2>&1; then
  echo "{\"additionalContext\": $(echo "$CONTEXT" | jq -Rs .)}"
else
  # 无 jq 时用 python
  python3 -c "import json,sys; print(json.dumps({'additionalContext': sys.stdin.read()}))" <<< "$CONTEXT"
fi

exit 0
