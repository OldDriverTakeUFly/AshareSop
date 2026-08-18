#!/usr/bin/env bash
# UserPromptSubmit: 识别认可信号 → 落 plan_approved 标记
# 当用户在 agent 展示方案后回复认可用语时，为后续代码写入解锁。
# SessionStart 时清除标记（每个 session 需要重新规划）。
set -euo pipefail

INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.prompt // empty')

MARKER_DIR="${ZCODE_PROJECT_DIR:-$(dirname "$0")/../..}/.zcode"
MARKER="$MARKER_DIR/.plan_approved"

# 认可信号词（用户确认方案的常见表达）
# 仅当 prompt 较短（<50字符）且含认可词时才落标——避免长对话中的误匹配
if [ ${#PROMPT} -lt 50 ] && echo "$PROMPT" | grep -qiE "^(好|好的|可以|ok|同意|批准|approved|做吧|开始|继续|go ahead|proceed|是的|对|嗯|行|没问题|approved plan)"; then
  mkdir -p "$MARKER_DIR"
  touch "$MARKER"
  echo "{\"additionalContext\": \"✅ 检测到规划认可信号，本 session 代码写入已解锁。\"}"
fi

exit 0
