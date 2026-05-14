# StockHot Dashboard 部署指南

绿联 NAS + Docker Compose + Cloudflare Tunnel，实现外网手机浏览器访问。

## 前置条件

- 绿联 NAS，已安装 Docker 和 Docker Compose
- Cloudflare 账号（免费版即可）
- 一个域名（DNS 托管在 Cloudflare）
- SSH 终端工具（Mac/Linux 自带，Windows 用 PowerShell 或 Git Bash）

> 不需要公网 IP，不需要端口转发，不需要 DDNS。

## 快速开始（5 分钟本地测试）

这一步不需要 Cloudflare 账号，先确认服务在局域网内能跑起来。

**1. SSH 连接到 NAS**

```bash
ssh your_nas_user@NAS_IP
```

**2. 进入项目目录，创建环境文件**

```bash
cd /path/to/CodeAgentDashboard
cp .env.template .env
```

`.env` 里默认值就能用，不用改。默认密码是 `stockhot/stockhot`。

**3. 启动服务**

```bash
docker compose up -d
```

第一次运行会构建镜像，需要几分钟。如果 NAS 是 ARM 架构（aarch64），不用担心，基础镜像都支持多架构。

三个容器会依次启动：

- `stockhot-backend`：FastAPI 后端，监听 8321 端口
- `stockhot-frontend`：Next.js 前端，监听 3000 端口
- `stockhot-tunnel`：Cloudflare 隧道（没配置 TUNNEL_TOKEN 会静默失败，不影响本地测试）

> 因为用了 `network_mode: host`，所有容器直接共享宿主机网络，不需要端口映射。SQLite 数据库通过 `./storage:/app/storage` 持久化到宿主机。

**4. 验证本地访问**

同一局域网的电脑或手机浏览器打开：

```
http://NAS_IP:3000
```

能看到页面就说明服务正常。用 `stockhot` / `stockhot` 登录。

如果需要单独检查后端健康状态：

```bash
curl http://localhost:8321/api/health
```

## Cloudflare Tunnel 配置（15 分钟）

本地测试通过后，配置隧道让外网也能访问。

**1. 登录 Cloudflare Zero Trust 面板**

打开 https://one.dash.cloudflare.com ，进入 **Networks → Tunnels**。

**2. 创建隧道**

- 点 **Create a tunnel**
- 名称填 `ugreen-nas`
- 选择 **Docker** 部署方式（页面会给出 docker run 命令，不用管它，我们用 docker compose）

**3. 复制 Tunnel Token**

创建完成后页面会显示一个 Token（`eyJhIjoi...` 开头的长字符串），复制它。

**4. 写入 .env**

```bash
# 编辑项目根目录下的 .env 文件
nano .env
```

找到 `TUNNEL_TOKEN` 那行，取消注释并填入 Token：

```
TUNNEL_TOKEN=eyJhIjoi你复制的token...
```

**5. 配置公共主机名**

在 Cloudflare Tunnel 页面，点刚创建的隧道进入 **Public Hostname** 标签：

- Subdomain: `dashboard`
- Domain: 选你的域名
- Service Type: `HTTP`
- URL: `localhost:3000`

保存。

**6. 重启服务让隧道生效**

```bash
docker compose up -d
```

检查隧道容器状态：

```bash
docker logs stockhot-tunnel
```

看到 `Registered tunnel connection` 就说明隧道已连通。

**7. 更新 CORS 配置**

编辑 `.env`，把你的域名加到 CORS 白名单：

```
CORS_ORIGINS=http://localhost:3000,https://dashboard.yourdomain.com
```

然后重启：

```bash
docker compose restart frontend
```

## 安全加固：Cloudflare Access（10 分钟）

隧道建好了，但现在是任何人都能访问。用 Cloudflare Access 加一层身份验证。

**1. 进入 Access → Applications**

在 Zero Trust 面板，进入 **Access → Applications → Create an application**。

选 **Self-hosted**。

**2. 配置应用**

- Application name: `StockHot Dashboard`
- Session Duration: `24 hours`
- Application domain: `dashboard.yourdomain.com`

**3. 创建访问策略**

- Policy name: `Only Me`
- Action: `Allow`
- Include 规则: 选 **Emails**，填你自己的邮箱地址

**4. 配置登录方式**

进入 **Access → Authentication**，启用你习惯的登录方式：

- Google 一键登录（最简单）
- 或 GitHub

配置完后，访问 `https://dashboard.yourdomain.com` 会先跳转到 Cloudflare 的登录页面，验证通过后才能进入 Dashboard。

## 验证

手机浏览器打开：

```
https://dashboard.yourdomain.com
```

预期流程：

1. Cloudflare Access 登录页 → 用 Google/GitHub 登录
2. StockHot Dashboard 登录页 → 输入 `stockhot` / `stockhot`
3. 看到仪表盘主页面

走到第 3 步就全部搞定了。

> 后续建议：把默认密码 `stockhot` 改掉。编辑 `.env` 里的 `STOCKHOT_API_PASSWORD`，然后 `docker compose restart backend`。

## 常见问题

### Docker 拉取镜像慢或失败

国内网络环境可能导致镜像拉不下来。几个办法：

- NAS 系统设置里配置 Docker 镜像加速器
- 手动拉取：`docker pull cloudflare/cloudflared:latest`
- 构建阶段慢的话多试几次，网络波动是暂时的

### 隧道容器反复重启

```bash
docker logs stockhot-tunnel
```

常见原因：

- **Token 为空**：检查 `.env` 里 `TUNNEL_TOKEN` 是否取消注释并填了值
- **Token 错误**：重新从 Cloudflare 面板复制，注意不要多复制空格或换行

### NAS 防火墙拦截端口

如果局域网内访问 `http://NAS_IP:3000` 不通：

- 检查绿联 NAS 管理界面里的防火墙设置
- 确认 3000 和 8321 端口没有被拦截
- 有些 NAS 固件默认只开放几个常用端口，需要手动放行

### 前端页面能打开，但数据加载失败

后端没启动或 CORS 配置不对：

```bash
# 检查后端状态
docker logs stockhot-backend

# 检查健康接口
curl http://localhost:8321/api/health
```

如果走域名访问，确认 `.env` 里 `CORS_ORIGINS` 包含你的域名。

### 数据持久化

SQLite 数据库文件在 `./storage/` 目录下。只要这个目录还在，删掉容器重建也不会丢数据。

备份方法：

```bash
cp -r storage/ storage_backup_$(date +%Y%m%d)/
```

### 完全卸载

```bash
docker compose down -v
# storage 目录不会自动删除，手动清理：
rm -rf storage/
```
