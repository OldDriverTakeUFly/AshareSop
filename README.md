# StockHot-CN — A股每日热点分析工具

自动采集 A 股市场数据，AI 智能分析热点主题，生成可视化报告，辅助个人投资者日常决策。

## 功能模块

| 模块 | 说明 |
|------|------|
| 涨停分析 | 涨停池、炸板池、跌停池、连板统计、板块联动、封单排名 |
| 龙虎榜 | 异动详情、机构席位、营业部、游资动向 |
| 资金流向 | 大盘资金流、板块资金流、趋势判断 |
| 风险提示 | ST 股票、停复牌、异常波动、资金出逃 |
| 投资 SOP | 盘前预研、持仓管理、图表分析、周期报告 |

## 技术栈

- **后端**: Python 3.12 + FastAPI + SQLite (aiosqlite) + AKShare
- **前端**: Next.js 15 (App Router) + React Query + shadcn/ui + Tailwind CSS
- **部署**: Docker + docker-compose（NAS 部署）
- **数据采集**: 定时任务 + AKShare + 碳酸锂/多晶硅期货数据

## 项目结构

```
stockhot/                 # Python 后端
├── api/                  # FastAPI 路由 + schemas
├── core/                 # 配置、日志、异常处理
├── data_collector/       # 数据采集模块
├── invest_sop/           # 投资 SOP（盘前、持仓、报告）
│   └── scripts/          # 定时任务脚本
├── storage/              # 数据库初始化
├── limit_up/             # 涨停分析
├── dragon_tiger/         # 龙虎榜分析
├── fund_flow/            # 资金流向分析
├── risk_alert/           # 风险提示
├── image_generator/      # 报告图片生成
└── research_report/      # 研究报告生成

dashboard/                # Next.js 前端
├── app/                  # 页面路由（App Router）
├── lib/                  # API 客户端 + hooks + types
└── components/           # UI 组件（shadcn/ui）

storage/                  # 运行时数据（gitignore）
├── database/stockhot.db  # SQLite 数据库
└── files/                # 图片 + 报告文件

docker/                   # Docker 部署配置
docs/                     # 研究报告 + 项目文档
tests/                    # 测试（98 tests）
```

## 快速开始

### 后端

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
uvicorn stockhot.api.main:app --port 8321
```

### 前端

```bash
cd dashboard
npm install
npm run dev
```

### 环境变量

复制 `.env.template` 为 `.env`，填入：
- `STOCKHOT_USERNAME` / `STOCKHOT_PASSWORD` — API Basic Auth
- `DEEPSEEK_API_KEY` — AI 分析（可选）

## 文档

- [`SPEC.md`](SPEC.md) — 项目需求规格说明书
- [`docs/`](docs/) — 行业研究报告
- [`docker/DEPLOY.md`](docker/DEPLOY.md) — NAS 部署指南
- [`.agents/skills/`](.agents/skills/) — Agent 开发技能规范（ZCode 自动发现的 skill 目录）
