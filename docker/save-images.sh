#!/usr/bin/env bash
# ============================================================
# StockHot Dashboard — 导出 Docker 镜像用于离线搬运
#
# 在能访问 Docker Hub 的机器上运行此脚本：
#   ./docker/save-images.sh [输出目录]
#
# 默认输出到当前目录，生成：
#   stockhot-backend.tar.gz
#   stockhot-frontend.tar.gz
#   images.sha256
# ============================================================
set -euo pipefail

OUTPUT_DIR="${1:-.}"
mkdir -p "$OUTPUT_DIR"

echo "============================================"
echo "  StockHot Dashboard — 镜像导出工具"
echo "============================================"
echo ""

# --------------------------------------------------
# 1. 构建
# --------------------------------------------------
echo "[1/4] 构建 Docker 镜像..."
docker compose build
echo ""

# --------------------------------------------------
# 2. 获取镜像 ID 和自动生成的名称
# --------------------------------------------------
echo "[2/4] 获取镜像信息..."

BACKEND_ID=$(docker compose images -q backend 2>/dev/null | head -1)
FRONTEND_ID=$(docker compose images -q frontend 2>/dev/null | head -1)

if [ -z "$BACKEND_ID" ] || [ -z "$FRONTEND_ID" ]; then
    echo "❌ 无法获取镜像 ID，请确认 docker compose build 成功"
    exit 1
fi

BACKEND_REPO=$(docker inspect --format '{{index .RepoDigests 0}}' "$BACKEND_ID" 2>/dev/null || docker inspect --format '{{index .RepoTags 0}}' "$BACKEND_ID" 2>/dev/null || echo "backend:$BACKEND_ID")
FRONTEND_REPO=$(docker inspect --format '{{index .RepoDigests 0}}' "$FRONTEND_ID" 2>/dev/null || docker inspect --format '{{index .RepoTags 0}}' "$FRONTEND_ID" 2>/dev/null || echo "frontend:$FRONTEND_ID")

echo "  后端镜像: $BACKEND_ID"
echo "  前端镜像: $FRONTEND_ID"
echo ""

# --------------------------------------------------
# 3. 打 tag 为固定名称（方便 load 后 compose 识别）
# --------------------------------------------------
echo "[3/4] 打 tag..."
docker tag "$BACKEND_ID" stockhot-backend:latest
docker tag "$FRONTEND_ID" stockhot-frontend:latest
echo ""

# --------------------------------------------------
# 4. 导出为压缩文件
# --------------------------------------------------
echo "[4/4] 导出镜像文件（需要几分钟）..."

echo "  → stockhot-backend.tar.gz ..."
docker save stockhot-backend:latest | gzip > "${OUTPUT_DIR}/stockhot-backend.tar.gz"

echo "  → stockhot-frontend.tar.gz ..."
docker save stockhot-frontend:latest | gzip > "${OUTPUT_DIR}/stockhot-frontend.tar.gz"

echo ""

# --------------------------------------------------
# 5. 生成校验文件
# --------------------------------------------------
cd "$OUTPUT_DIR"
sha256sum stockhot-backend.tar.gz stockhot-frontend.tar.gz > images.sha256
cd - > /dev/null

# --------------------------------------------------
# 6. 输出汇总
# --------------------------------------------------
BACKEND_SIZE=$(du -h "${OUTPUT_DIR}/stockhot-backend.tar.gz" | cut -f1)
FRONTEND_SIZE=$(du -h "${OUTPUT_DIR}/stockhot-frontend.tar.gz" | cut -f1)

echo "============================================"
echo "  导出完成！"
echo "============================================"
echo ""
echo "文件清单："
echo "  ${OUTPUT_DIR}/stockhot-backend.tar.gz   ($BACKEND_SIZE)"
echo "  ${OUTPUT_DIR}/stockhot-frontend.tar.gz  ($FRONTEND_SIZE)"
echo "  ${OUTPUT_DIR}/images.sha256"
echo ""
echo "--------------------------------------------"
echo "  搬运到 NAS（任选一种方式）："
echo "--------------------------------------------"
echo ""
echo "  方式一：SCP 传输"
echo "    scp ${OUTPUT_DIR}/stockhot-*.tar.gz ${OUTPUT_DIR}/images.sha256 your_nas_user@NAS_IP:/tmp/"
echo ""
echo "  方式二：U 盘 / 共享文件夹"
echo "    把以上 3 个文件复制到 NAS 能访问的位置"
echo ""
echo "--------------------------------------------"
echo "  在 NAS 上加载镜像："
echo "--------------------------------------------"
echo ""
echo "    bash docker/load-images.sh /tmp"
echo "    docker compose up -d"
echo ""
