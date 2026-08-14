#!/usr/bin/env bash
# 清理 crontab 里 INVEST_SOP block 的重复条目
# 用法：bash stockhot/invest_sop/dedup_crontab.sh [--check]
#   --check 只检查不修改

set -euo pipefail

CHECK_ONLY=false
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=true

MARKER="# INVEST_SOP_CRON_START"
MARKER_END="# INVEST_SOP_CRON_END"

CURRENT=$(crontab -l 2>/dev/null || true)

if [ -z "$CURRENT" ]; then
    echo "[ERROR] crontab 读取失败或为空"
    exit 1
fi

# 统计 marker 出现次数
MARKER_COUNT=$(echo "$CURRENT" | grep -cF "$MARKER" || true)
BLOCK_COUNT=$(echo "$CURRENT" | grep -cF "$INVEST_SOP" || true)

echo "检测到 $MARKER_COUNT 个 INVEST_SOP block"
echo ""

if [ "$MARKER_COUNT" -le 1 ]; then
    echo "✅ 无重复，无需清理"
    exit 0
fi

echo "⚠️ 发现 $MARKER_COUNT 个重复 block！"
echo ""
echo "=== 当前所有 INVEST_SOP 条目 ==="
echo "$CURRENT" | grep -nE "invest_sop|studies/|scripts/" | head -40
echo ""

if $CHECK_ONLY; then
    echo "[CHECK-ONLY] 如需清理，去掉 --check 参数运行"
    exit 0
fi

# 提取所有 block（marker 到 marker_end）
# 保留第一个 block，删除后续重复的
python3 - << 'PYEOF'
import subprocess
import sys

result = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
lines = result.stdout.split('\n')

MARKER = "# INVEST_SOP_CRON_START"
MARKER_END = "# INVEST_SOP_CRON_END"

new_lines = []
in_block = False
block_seen = False
removed = 0

i = 0
while i < len(lines):
    line = lines[i]
    if MARKER in line:
        if block_seen:
            # 已经有一个 block，跳过这个重复的（到 END 为止）
            while i < len(lines) and MARKER_END not in lines[i]:
                i += 1
            removed += 1
            i += 1  # 跳过 END 行
            continue
        else:
            block_seen = True
            in_block = True
            new_lines.append(line)
    elif MARKER_END in line and in_block:
        in_block = False
        new_lines.append(line)
    else:
        new_lines.append(line)
    i += 1

new_crontab = '\n'.join(new_lines)
# 写回
proc = subprocess.run(['crontab', '-'], input=new_crontab, capture_output=True, text=True)
if proc.returncode != 0:
    print(f"[ERROR] 写入失败: {proc.stderr}")
    sys.exit(1)

print(f"✅ 已删除 {removed} 个重复 block")
print("验证：")
verify = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
count = verify.stdout.count(MARKER)
print(f"  剩余 block 数: {count}")
PYEOF
