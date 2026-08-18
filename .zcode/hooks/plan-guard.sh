#!/usr/bin/env bash
# PreToolUse guard: 代码修改前需规划认可（探索性任务可跳过）
# 设计：session 内首次代码写入触发"ask"确认，认可后本 session 不再拦截。
# 排除：非代码文件（md/json/sh）、测试文件、.zcode/ 自身、docs/。
set -euo pipefail

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# 无文件路径则放行
[ -z "$FILE_PATH" ] && exit 0

# 非代码文件放行（文档/配置/脚本/hooks 自身）
echo "$FILE_PATH" | grep -qE '\.(md|json|yaml|yml|toml|txt|log|csv|sh)$' && exit 0
echo "$FILE_PATH" | grep -qE '/\.zcode/|/docs/|/\.git/' && exit 0

# 测试文件放行（测试通常跟随已认可的实现）
echo "$FILE_PATH" | grep -qE '/tests?/|test_.*\.py$' && exit 0

# session 标记：已认可则放行
MARKER="${ZCODE_PROJECT_DIR:-$(dirname "$0")/../..}/.zcode/.plan_approved"
[ -f "$MARKER" ] && exit 0

# 首次代码写入 → ask（用户点"允许"即视为规划认可，本 session 不再拦截）
cat <<'EOF'
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permission": {
      "decision": "ask",
      "reason": "📋 规划检查：这是本 session 首次修改代码文件。请确认已对需求做过规划并获得认可。\n\n• 已规划/探索性任务/小修复 → 点击允许继续\n• 未规划 → 取消后先展示实施方案\n\n（允许后本 session 内不再拦截）"
    }
  }
}
EOF
exit 0
