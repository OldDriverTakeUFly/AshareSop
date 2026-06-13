#!/bin/bash
# Davis Analyzer WebUI — 一键启动脚本
set -e
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "🚀 启动 Davis Analyzer WebUI..."

if [ ! -f .env ]; then
    echo "⚠️  未找到 .env 文件，请在项目根目录创建并设置 TUSHARE_TOKEN"
    exit 1
fi

echo "📡 启动后端 (port 8322)..."
uvicorn davis_webui.backend.main:app --port 8322 --reload &
BACKEND_PID=$!

echo "🖥️  启动前端 (port 3001)..."
cd davis_webui/frontend
npm run dev &
FRONTEND_PID=$!

echo ""
echo "✅ 服务已启动:"
echo "   前端: http://localhost:3001"
echo "   后端: http://localhost:8322/api/health"
echo ""
echo "按 Ctrl+C 停止所有服务"

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT TERM
wait
