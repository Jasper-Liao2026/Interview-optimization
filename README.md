# 简历优化器

批量生产**岗位适配版简历**的工具。解决 BOSS 直聘海投时「一份简历打天下、逐份手改不可行」的问题。

> 当前进度：**M0 / M1 / M2 已完成，M3 功能已交付**。M0 是脚手架（含 M0-8 Langfuse 观测），
> M1 是第一条端到端垂直切片 —— 「一条经历 + 一个 JD → 生成 → 预览 → 导出 PDF」，
> M2 是**素材库** —— 经历条目的完整录入、编辑、分组浏览，量化结果与多版本表述；
> M3 是 **JD 解析与匹配** —— 文本/截图输入、岗位管理、向量粗筛、事实证据矩阵与生成衔接。
> 验收：`node scripts/verify-m1.mjs` → 13 通过 / 0 失败 / 0 跳过；
> `node scripts/verify-m2.mjs` → 16 通过 / 0 失败 / 0 跳过；M3 API/Postgres 验收 20 通过。
> 真实文本 JD 评测 10/10；真实截图与语义向量质量待配置模型后验收，见 [`M3 总结`](docs/M3-summary.md)。
> 任务全貌见 [`tasks.md`](tasks.md)，选型依据见 [`docs/tech-stack.md`](docs/tech-stack.md)。
>
> **定位**：本地自托管的开源工具 —— 不提供线上服务，**不上线**；单机单用户，数据只存在你自己机器上。

---

## 技术栈

| 层 | 选型 | 职责 |
|---|---|---|
| 前端 | Next.js 15 App Router + TypeScript + Tailwind v4 | UI 渲染、消费流式响应 |
| 后端 | FastAPI + Python 3.12（uv 管依赖） | **全部业务逻辑** |
| Agent 编排 | LangGraph（M4 起） | StateGraph、Checkpointer、interrupt |
| 数据 | 本地 Postgres 16 + pgvector（Docker，无外部依赖） | 业务数据、向量检索 |
| 观测 | Langfuse（M0-8 起） | trace、dataset、LLM-as-judge |
| 本地编排 | docker compose | web + api + postgres |

**架构决策**：前端只做壳，业务逻辑与 agent 编排全在 Python 侧。
LangGraph 在持久化编排（checkpointer、interrupt、条件边回环、time-travel 调试）上的成熟度是选择它的主要原因，不是「因为更熟悉 Python」的妥协。

---

## 快速开始

### 前置

Node ≥ 22、pnpm ≥ 11、Docker Desktop（本地编排用）。
**Python 与 uv 无需预装** —— `pnpm api:setup` 会把 uv 装进 `scripts/.tools/uv`，不碰全局环境。

### 起步

```bash
pnpm install          # 前端依赖
pnpm api:setup        # 建 uv 工具链 + 按 pyproject 装后端依赖
pnpm gen:types        # Pydantic → OpenAPI → TS 类型
```

### 日常开发

```bash
pnpm db:up            # 只起 postgres（含 migration + seed）
pnpm api:dev          # 后端 http://localhost:8000（热重载）
pnpm web              # 前端 http://localhost:3000（热重载）
```

打开 <http://localhost:3000> 应看到 **M0 自检台**：三段链路（Next.js → FastAPI → Postgres）状态，以及 `service_meta` 表里的数据。
导航栏提供以下入口：

- **`/library`** —— M2 的素材库：新增 / 编辑 / 删除经历条目，按实习 · 项目 · 校园分组浏览；
  每条可填「量化结果」（指标名 / 数值 / 口径）与「多版本表述」（同一经历按岗位方向存多份写法）。
- **`/generate`** —— M1 的生成页：粘 JD、勾经历、生成、iframe 预览、导出 PDF。
- **`/jobs`** —— M3 的岗位匹配页：文本/截图解析、JD 管理、匹配矩阵、事实证据与缺口；勾选经历后进入生成。

### 已有数据库升级到 M3

初始化 SQL 只在数据库卷为空时执行。已有 M2 数据库执行下面的增量迁移，保留现有数据：

```bash
docker cp supabase/migrations/20261011000000_m3_matching.sql resume-postgres:/tmp/m3-matching.sql
docker exec resume-postgres psql -U postgres -d resume_optimizer -v ON_ERROR_STOP=1 -f /tmp/m3-matching.sql
```

若数据库仍停留在 M1，先以同样方式执行 `20261010000000_m2_library.sql`，再执行 M3。

### 文本、截图和语义向量模型

本地 API 配置位于 `apps/api/.env`，完整模板见 `.env.example`。三类服务可独立配置：

| 配置前缀 | 用途 | 默认行为 |
|---|---|---|
| `LLM_*` | JD 文本解析和简历生成 | `stub` 演示 |
| `VISION_*` | 多模态直读截图 | 未设置时复用 `LLM_*`，模型须支持视觉 |
| `EMBEDDING_*` | `/embeddings` 语义向量 | `stub` 哈希演示，固定 1536 维 |

真实服务使用 `*_PROVIDER=openai-compatible` 并填写 `*_BASE_URL`、`*_API_KEY`、`*_MODEL`。
stub 不识别截图；哈希向量不代表真实语义质量。配置示例与验收命令见 [`M3 总结`](docs/M3-summary.md)。

### 一键全起（M0-4 验收）

```bash
pnpm stack:up         # docker compose up -d --build
pnpm stack:logs
pnpm stack:down
```

### 常用命令

| 命令 | 说明 |
|---|---|
| `pnpm test` | 后端 pytest |
| `pnpm lint` | ruff check + ESLint |
| `pnpm format` | ruff format |
| `pnpm typecheck` | 全仓 TS 类型检查 |
| `pnpm gen:types` | 刷新前端接口类型（**改完 Pydantic 必跑**） |
| `pnpm db:reset` | 重建 postgres 卷并重放 migration |
| `pnpm verify:m0` / `pnpm verify:m1` / `pnpm verify:m2` / `pnpm verify:m3` | 一键复现对应里程碑的验收结论 |
| `pnpm eval:m3` | 真实文本模型 10 份 JD 评测；加 `--images --embeddings` 验收视觉与语义服务 |

---

## 目录结构

```
resume-optimizer/
├── apps/
│   ├── web/                     # Next.js 15：UI 壳，不含业务逻辑
│   │   └── src/
│   │       ├── app/             # App Router
│   │       └── lib/             # env / 类型化 API 客户端
│   └── api/                     # FastAPI：业务逻辑 + agent 编排
│       ├── app/
│       │   ├── agents/          # JD 解析 / 改写 / 组装（M4 搬进 LangGraph）
│       │   ├── llm/             # LLM 调用层（stub + OpenAI 兼容）
│       │   ├── observability/   # Langfuse 接入（可降级）
│       │   ├── pdf/             # 无头 Chromium 导出（M1-6）
│       │   ├── render/          # 简历 HTML 模板（★ 预览与 PDF 的同一份来源）
│       │   ├── repositories/    # 仓储层（asyncpg）
│       │   ├── routers/         # HTTP 路由
│       │   ├── schemas/         # ★ Pydantic model = 接口类型单一定义源
│       │   ├── services/        # 业务服务（M1 起）
│       │   ├── config.py        # 配置集中处
│       │   ├── db.py            # 数据访问层
│       │   ├── tracing.py       # trace_id 贯穿
│       │   └── main.py          # 应用装配
│       ├── scripts/             # 运维脚本（OpenAPI 导出等）
│       └── tests/
├── packages/
│   └── api-types/               # ★ OpenAPI → TS 生成产物
├── supabase/                    # 纯 SQL 目录（Supabase 兼容布局，运行时不需要 Supabase）
│   ├── migrations/              # 表结构变更的唯一来源
│   └── seed.sql
├── scripts/                     # 根级工程脚本（Node，跨平台）
├── docs/                        # tech-stack / M0·M1·M2·M3 总结 / PDF 方案对比
└── docker-compose.yml
```

---

## 三条硬约定

破了任意一条，双语言项目的摩擦会立刻回到最痛的状态：

1. **Pydantic model 是接口类型的单一定义源** —— 前端类型一律由 `pnpm gen:types` 生成，禁止手写
2. **流式端点由浏览器直连 FastAPI** —— 不让 Next.js 转发 SSE（转发必须关闭响应缓冲，否则逐字输出会退化成整段吐出）
3. **预览与 PDF 用同一份模板 HTML** —— `/html` 与 `/pdf` 同源渲染，「所见即所得」是结构保证，不靠人工比对维持

细节与代价分析见 [`docs/tech-stack.md`](docs/tech-stack.md)。

---

## 文档

| 文档 | 内容 |
|---|---|
| [`requirements.md`](requirements.md) | 需求分析、核心链路、风险清单、待商榷项 |
| [`tasks.md`](tasks.md) | 9 个里程碑、57 个任务、推进节奏 |
| [`docs/tech-stack.md`](docs/tech-stack.md) | 技术选型定案、五项代价与缓解措施 |
| [`docs/M0-summary.md`](docs/M0-summary.md) | **M0 交付总结**：架构概览、验收结果、遗留问题、面试要点 |
| [`docs/M1-summary.md`](docs/M1-summary.md) | **M1 交付总结**：垂直切片、验收数据、踩坑记录、面试要点 |
| [`docs/M2-summary.md`](docs/M2-summary.md) | **M2 交付总结**：素材库、量化结果与多版本表述的建模动机、PUT 语义取舍 |
| [`docs/M3-summary.md`](docs/M3-summary.md) | **M3 交付总结**：JD 管理、截图协议、事实匹配与向量缓存、验收边界 |
| [`docs/M1-pdf-export-comparison.md`](docs/M1-pdf-export-comparison.md) | PDF 导出三方案对比：四维矩阵 + 量化证据 + 复现命令 |
