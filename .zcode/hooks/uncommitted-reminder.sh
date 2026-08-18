#!/usr/bin/env bash
# Stop: 回合结束时检查未提交变更（防止工作丢失）
cd "${CLAUDE_PROJECT_DIR:-${ZCODE_PROJECT_DIR:-$(dirname "$0")/../..}}" 2>/dev/null || true

DIRTY=$(git status --porcelain 2>/dev/null | grep -v "^??" | wc -l || echo "0")
if [ "${DIRTY:-0}" -gt 0 ] 2>/dev/null; then
  MSG="📌 提醒：有 ${DIRTY} 个已修改未提交的文件。如工作已完成，考虑 git commit。"
  if command -v jq >/dev/null 2>&1; then
    echo "{\"additionalContext\": $(echo "$MSG" | jq -Rs .)}"
  else
    python3 -c "import json,sys; print(json.dumps({'additionalContext': sys.stdin.read()}))" <<< "$MSG"
  fi
fi

exit 0
