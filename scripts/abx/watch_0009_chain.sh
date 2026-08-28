#!/bin/bash
# 实验0009 watcher: 等全期A/B完成 → 自动链式启动随机窗口测试台(全变体).
# 背景: 本会话由 cron 派生无法再建 cron(2026-08-28), 用进程 watcher 串联两级长跑.
# 判定与归档由下次交互的会话按预注册判定线执行(见 pb_struct_exempt_abx.py 头注).
cd /home/leo/Projects/CodeAgentDashboard
echo "[watcher] $(date '+%F %T') 启动, 等待 A/B (PID 287331) 完成"
while pgrep -f "pb_struct_exempt_abx" > /dev/null; do sleep 60; done
sleep 30
if grep -q "E1_pbstruct" logs/abx/pb_struct_exempt_abx.json 2>/dev/null \
   && grep -q "E2_pbstruct" logs/abx/pb_struct_exempt_abx.json 2>/dev/null; then
  echo "[watcher] $(date '+%F %T') A/B 完成, 启动测试台 (baseline,E1,E2)"
  setsid nohup .venv/bin/python scripts/abx/harness_0009.py \
    --variants baseline,E1_pbstruct,E2_pbstruct \
    > logs/abx/harness_0009_run.log 2>&1 &
  echo "[watcher] harness 已启动"
else
  echo "[watcher] $(date '+%F %T') A/B 未完整产出两变体(可能中途死亡), 不启动测试台, 等人工判定"
fi
