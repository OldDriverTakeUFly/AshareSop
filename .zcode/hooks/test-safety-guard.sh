#!/usr/bin/env bash
# PreToolUse guard: 拦截可能触发真实飞书推送的测试命令
# 背景：2026-08-18 另一个 session 跑 pytest 时未 mock feishu_bot，
#       导致测试消息涌入生产飞书群（多条 + 历史日期）。
set -euo pipefail

INPUT=$(cat)
CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

# 检测 pytest 且涉及 feishu 相关模块
if echo "$CMD" | grep -qiE "pytest.*feishu|feishu.*test|test.*feishu"; then
  cat <<'EOF'
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permission": {
      "decision": "ask",
      "reason": "⚠️ 检测到 pytest + feishu 组合。请确认测试已 mock stockhot.notification.feishu_bot（参考 tests/test_feishu_bot.py 的 MockTransport 模式），否则测试消息会推到生产飞书群。"
    }
  }
}
EOF
  exit 0
fi

# 检测直接运行含飞书推送的脚本（非 --dry-run 模式）
if echo "$CMD" | grep -qE "push_eod_feishu|premarket_strategy|inject_screen" && ! echo "$CMD" | grep -qE "\-\-dry-run|\-\-no-feishu"; then
  cat <<'EOF'
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permission": {
      "decision": "ask",
      "reason": "⚠️ 此脚本会推送飞书。建议加 --dry-run（inject/premarket）或 --no-feishu（eod_review）测试。确认要推生产群吗？"
    }
  }
}
EOF
  exit 0
fi

exit 0
