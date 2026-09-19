# 归档区

本目录存放已停用项目,git 历史完整,复活方法 = `git mv` 回原位。

| 目录/文件 | 原顶层路径 | 归档日期 | 原因 |
|---|---|---|---|
| dashboard/ | dashboard/ | 2026-09-19 | stockhot 数据看板(Next.js+TS),2026-05 后未动 |
| test-results/ | test-results/ | 2026-09-19 | 看板时代 vitest 产物,2026-05 后未动 |
| public/ | public/ | 2026-09-19 | 早期静态前端(vanilla JS),2026-05 后未动 |
| docker-compose.yml + docker/ | 同名根路径 | 2026-09-19 | 看板集群容器编排(backend/frontend/cloudflared),与现行 Python 单仓无引用 |
| davis_webui/ + docker-compose.davis.yml + .env.davis + *-davis-quick-tunnel.sh | 同名根路径 | 2026-09-19 | davis Web 界面(FastAPI+Next.js),2026-07 后未动;systemd 服务已 stop+disable,unit 已删 |

注:根 `src/` 经查证仅为 `stockhot.egg-info` 构建残留(gitignored),已直接删除未归档。
注:根 `package.json`/`node_modules/` 曾随看板集群归档,实测发现是 `scripts/card_factory/snap.cjs`(cardgen 截图链)的 playwright 活依赖,已恢复留根。
