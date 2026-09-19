#!/usr/bin/env bash
# ============================================================
# StockHot Dashboard — 从 tar.gz 文件加载 Docker 镜像
#
# 在 NAS（目标机器）上运行：
#   bash docker/load-images.sh [镜像文件目录]
#
# 默认从当前目录读取：
#   stockhot-backend.tar.gz
#   stockhot-frontend.tar.gz
# ============================================================
set -euo pipefail

IMAGE_DIR="${1:-.}"

echo "============================================"
echo "  StockHot Dashboard — 镜像加载工具"
echo "============================================"
echo ""

# --------------------------------------------------
# 0. 检查文件存在
# --------------------------------------------------
for f in stockhot-backend.tar.gz stockhot-frontend.tar.gz; do
    if [ ! -f "${IMAGE_DIR}/${f}" ]; then
        echo "❌ 找不到 ${IMAGE_DIR}/${f}"
        echo ""
        echo "请把镜像文件放到正确位置，或指定目录："
        echo "  bash docker/load-images.sh /path/to/image/dir"
        exit 1
    fi
done

# --------------------------------------------------
# 1. 校验文件完整性（如果 sha256 文件存在）
# --------------------------------------------------
if [ -f "${IMAGE_DIR}/images.sha256" ]; then
    echo "[1/3] 校验文件完整性..."
    cd "$IMAGE_DIR"
    if sha256sum -c images.sha256 --status 2>/dev/null; then
        echo "  ✅ 校验通过"
    else
        echo "  ❌ 校验失败！文件可能传输损坏"
        echo "  重新从构建机器复制文件后再试"
        exit 1
    fi
    cd - > /dev/null
else
    echo "[1/3] 跳过校验（未找到 images.sha256）"
fi
echo ""

# --------------------------------------------------
# 2. 加载镜像
# --------------------------------------------------
echo "[2/3] 加载镜像（需要几分钟）..."

echo "  → 加载 stockhot-backend ..."
docker load < "${IMAGE_DIR}/stockhot-backend.tar.gz"

echo "  → 加载 stockhot-frontend ..."
docker load < "${IMAGE_DIR}/stockhot-frontend.tar.gz"

echo ""

# --------------------------------------------------
# 3. 验证
# --------------------------------------------------
echo "[3/3] 验证镜像..."

BACKEND_OK=$(docker images stockhot-backend:latest --format "{{.ID}}" | head -1)
FRONTEND_OK=$(docker images stockhot-frontend:latest --format "{{.ID}}" | head -1)

if [ -n "$BACKEND_OK" ] && [ -n "$FRONTEND_OK" ]; then
    echo "  ✅ stockhot-backend:latest  ($BACKEND_OK)"
    echo "  ✅ stockhot-frontend:latest ($FRONTEND_OK)"
else
    echo "  ❌ 镜像未正确加载"
    docker images | grep stockhot || true
    exit 1
fi

# --------------------------------------------------
# 4. 拉取 cloudflared（公开镜像，需要网络）
# --------------------------------------------------
echo ""
echo "--------------------------------------------"
echo "  cloudflared 镜像处理"
echo "--------------------------------------------"
echo ""
echo "  cloudflared 是公开镜像，需要 NAS 能访问 Docker Hub。"
echo "  如果 NAS 网络不通，也可以手动离线导入："
echo ""
echo "    # 在能上网的机器上："
echo "    docker pull cloudflare/cloudflared:latest"
echo "    docker save cloudflare/cloudflared:latest | gzip > cloudflared.tar.gz"
echo ""
echo "    # 然后拷到 NAS 上加载："
echo "    docker load < cloudflared.tar.gz"
echo ""

# --------------------------------------------------
# 5. 输出下一步
# --------------------------------------------------
echo "============================================"
echo "  镜像加载完成！"
echo "============================================"
echo ""
echo "  下一步："
echo ""
echo "    # 1. 创建环境文件"
echo "    cp .env.template .env"
echo "    # 编辑 .env，设置 TUNNEL_TOKEN 等"
echo ""
echo "    # 2. 启动服务（使用 docker compose up 会跳过构建，直接用已加载的镜像）"
echo "    # 注意：需要先给 compose 指定 image 名称，修改 docker-compose.yml："
echo "    #   backend.services.image: stockhot-backend:latest"
echo "    #   frontend.services.image: stockhot-frontend:latest"
echo "    docker compose up -d"
echo ""
echo "    # 3. 验证"
echo "    curl http://localhost:8321/api/health"
echo "    curl http://localhost:3000"
echo ""
