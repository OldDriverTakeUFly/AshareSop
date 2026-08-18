#!/usr/bin/env bash
# PostToolUse on Bash: 长输出后提示截断技巧（节省后续 token）
# 背景：本会话因行情查询/pytest 输出过长浪费大量 token。
set -euo pipefail

# 用 python 解析 JSON（jq 对控制字符敏感）
LINE_COUNT=$(python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    output = data.get('tool_output') or data.get('tool_result') or ''
    print(len(output.splitlines()))
except: print(0)
")

if [ "${LINE_COUNT:-0}" -gt 200 ] 2>/dev/null; then
  HINT="💡 上一次命令输出 ${LINE_COUNT} 行。下次考虑加 | tail -50 或 | grep 过滤以节省 token。"
  python3 -c "import json,sys; print(json.dumps({'additionalContext': sys.stdin.read()}))" <<< "$HINT"
fi

exit 0
