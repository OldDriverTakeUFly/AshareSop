#!/usr/bin/env bash
# Stop: 有未提交变更时提醒保存 session 状态 + git commit
# 同时在 context 较大时建议 /compact
set -euo pipefail
cd "${CLAUDE_PROJECT_DIR:-${ZCODE_PROJECT_DIR:-$(dirname "$0")/../..}}" 2>/dev/null || true

# 检查是否有重要的未提交变更（排除 untracked）
DIRTY=$(git status --porcelain 2>/dev/null | grep -v "^??" | wc -l || echo "0")
if [ "${DIRTY:-0}" -gt 0 ] 2>/dev/null; then
  MSG="📌 ${DIRTY} 个未提交文件。建议：1) git commit 保存进度 2) 更新 .zcode/session_state.md 记录当前状态（下次 session 快速恢复）"
  python3 -c "import json,sys; print(json.dumps({'additionalContext': sys.stdin.read()}))" <<< "$MSG" 2>/dev/null || true
fi

exit 0
