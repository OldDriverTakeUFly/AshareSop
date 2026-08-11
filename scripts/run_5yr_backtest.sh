#!/usr/bin/env bash
# 5 年回测一键执行脚本
#
# 步骤：
#   1. 预缓存财务数据（~2min）
#   2. 运行 5 年回测（~70min）
#
# 用法：
#   cd /home/leo/Projects/CodeAgentDashboard
#   bash scripts/run_5yr_backtest.sh          # 默认 200 只
#   UNIVERSE_SIZE=50 bash scripts/run_5yr_backtest.sh  # 快速验证

set -e
cd "$(dirname "$0")/.."

export PROJECT_ROOT="$(pwd)"
export PYTHONPATH="$PROJECT_ROOT"
export UNIVERSE_SIZE="${UNIVERSE_SIZE:-200}"

echo "================================================"
echo "  5 年回测一键执行 (UNIVERSE_SIZE=$UNIVERSE_SIZE)"
echo "================================================"
echo ""

# Step 1: 预缓存财务数据
echo "▶ Step 1/2: 预缓存财务数据..."
PYTHONPATH=. .venv/bin/python scripts/precache_financial_5yr.py

echo ""
echo "▶ Step 2/2: 运行 5 年回测..."
PYTHONPATH=. .venv/bin/python scripts/backtest_5yr_annual.py

echo ""
echo "✓ 完成！结果在 logs/backtest_5yr_annual.json"
