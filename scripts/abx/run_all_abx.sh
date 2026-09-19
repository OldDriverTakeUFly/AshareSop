#!/bin/bash
set -e
cd /home/leo/Projects/CodeAgentDashboard
export PROJECT_ROOT=/home/leo/Projects/CodeAgentDashboard
export PYTHONPATH=$PROJECT_ROOT

echo "=== $(date) 开始：MA120 A/B (含新基线 B0) ==="
.venv/bin/python scripts/ma120_abx.py
echo "=== $(date) MA120 完成 ==="

echo "=== $(date) 开始：iVIX A/B ==="
.venv/bin/python scripts/ivix_abx.py
echo "=== $(date) iVIX 完成 ==="

echo "=== $(date) 开始：共振 A/B ==="
.venv/bin/python scripts/synergy_abx.py
echo "=== $(date) 共振 完成 ==="

echo "=== $(date) 开始：超跌反弹 A/B ==="
.venv/bin/python scripts/bounce_abx.py
echo "=== $(date) 超跌反弹 完成 ==="

echo "=== $(date) 全部完成 ==="
