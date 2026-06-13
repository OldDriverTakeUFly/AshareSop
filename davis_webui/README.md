# Davis Analyzer WebUI

## 第一章：项目简介

基于戴维斯双击理论的A股估值筛选可视化应用。完全独立的 Web 应用，封装 davis_analyzer Python 包，提供浏览器交互式筛选、可视化分析和深度调研工作流。

---

## 第二章：系统架构

```
┌─────────────────┐     HTTP/API     ┌──────────────────┐     Python      ┌──────────────────┐
│   Browser       │ ◄──────────────► │  FastAPI Backend │ ◄─────────────► │  davis_analyzer  │
│  Next.js :3001  │                  │     :8322        │                 │   Tushare API    │
│                 │                  │                  │                 │                  │
│ ┌─────────────┐ │                  │ ┌──────────────┐ │                 │ ┌──────────────┐ │
│ │ 5 Pages     │ │                  │ │ 7 Routers    │ │                 │ │ Pipeline     │ │
│ │ 6 Components│ │                  │ │ 11 Endpoints │ │                 │ │ Scoring      │ │
│ └─────────────┘ │                  │ └──────────────┘ │                 │ │ Reports      │ │
└─────────────────┘                  └──────────────────┘                 └──────────────────┘
```

三层架构：

1. **浏览器层** — Next.js 15 前端，端口 3001，提供 5 个功能页面和 6 个可复用组件
2. **API 层** — FastAPI 后端，端口 8322，提供 11 个 REST 接口，7 个路由模块
3. **引擎层** — davis_analyzer Python 包，执行估值管线、评分计算、研报生成

---

## 第三章：环境要求

- Python 3.12+
- Node.js 18+
- Tushare Pro 账号（[注册地址](https://tushare.pro)）

---

## 第四章：快速开始

### 安装

在项目根目录执行：

```bash
pip install -e .
```

### 配置

在项目根目录创建 `.env` 文件，写入 Tushare API Token：

```
TUSHARE_TOKEN=your_token_here
```

### 启动后端

```bash
uvicorn davis_webui.backend.main:app --port 8322 --reload
```

### 启动前端

```bash
cd davis_webui/frontend
npm install
npm run dev
```

### 访问应用

打开浏览器访问 http://localhost:3001

### 一键启动

使用提供的便捷脚本同时启动前后端：

```bash
bash davis_webui/start.sh
```

---

## 第五章：功能页面

| 页面 | 路由 | 说明 |
|------|------|------|
| 筛选结果 | /screening | 运行筛选管线，查看 Top N 排序表 |
| 个股详情 | /stocks/{ts_code} | 雷达图 + 研报 + 详情 |
| 趋势可视化 | /trends/{ts_code} | PE/PB 月度趋势线图 |
| 困境热力图 | /distress | 3层×3信号热力图 |
| 深度调研 | /research | 清单生成→填写→重评 |

---

## 第六章：API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/health | 健康检查 |
| POST | /api/screening/start | 启动筛选管线 |
| GET | /api/screening/{task_id}/status | 查询筛选进度 |
| GET | /api/screening/{task_id}/results | 获取筛选结果 |
| GET | /api/stocks/{task_id}/{ts_code} | 获取个股详情 |
| GET | /api/reports/{task_id}/{ts_code} | 获取个股研报 |
| POST | /api/checklists/generate | 生成深度调研清单 |
| POST | /api/checklists/{ts_code}/fill | 填写调研清单 |
| POST | /api/checklists/rescore | 根据清单重新评分 |
| GET | /api/trends/{task_id}/{ts_code} | 获取趋势数据 |
| GET | /api/distress/{task_id} | 获取困境信号热力图数据 |

---

## 第七章：开发

### 后端测试

```bash
.venv/bin/python -m pytest davis_webui/backend/tests/ -v
```

### 前端构建

```bash
cd davis_webui/frontend
npm run build
```

---

## 第八章：技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| 后端 | FastAPI + Uvicorn + Pydantic | 异步 API 框架 + 数据校验 |
| 前端 | Next.js 15 + React 19 + TypeScript | App Router + 服务端组件 |
| 可视化 | Recharts 3 + CSS Grid | 雷达图、折线图、热力图 |
| 数据请求 | TanStack React Query | 服务端状态缓存与同步 |
| 样式 | Tailwind CSS 4 | 暗色主题 |

---

*本应用由戴维斯双击估值筛选系统驱动，仅供参考，不构成投资建议。*
