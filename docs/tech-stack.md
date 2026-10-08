# 简历优化器 — 技术选型

> 2026-10-08 定案｜方案：**A · 前端壳 + Python 全包**

---

## 1. 技术栈

| 层 | 选型 | 职责 |
|---|---|---|
| 前端 | Next.js 15 App Router + TypeScript | UI 渲染、认证、消费流式响应 |
| 流式 | Vercel AI SDK | 前端侧 SSE 消费与 React 集成 |
| 后端 | **FastAPI + Python 3.12** | 全部业务逻辑 |
| Agent 编排 | **LangGraph** | StateGraph、Checkpointer、interrupt |
| 数据 | Supabase（Postgres + pgvector + Auth） | 业务数据、向量检索、鉴权 |
| 观测与评测 | Langfuse | trace、dataset、LLM-as-judge |
| 部署 | 前端 Vercel / 后端 Fly.io 或 Railway | — |

---

## 2. 这个方案的真实优势

除了「后端用 Python 写」之外，还有一条容易被忽略的收益：

**LangGraph 在持久化编排上的成熟度明显高于 TS 侧。** 本项目的三个核心设计——评分循环、并行改写、失败恢复——恰恰都是 LangGraph 的主场：

- Checkpointer 天然支持中断恢复与多版本快照
- `interrupt()` 原生支持人工介入（精调模式的 AI 对话微调可以直接用）
- 条件边直接表达评分循环的判定与回环
- time-travel 调试可以回放任意一轮 agent 执行

**所以这个选择在「agent 技术力」这个维度上是加分的，不是妥协。**

---

## 3. 五项已知代价与缓解措施

必须在开发前就把这些约定定死，否则摩擦会持续累积。

### 代价一：类型要手写两遍

**缓解**：以 Pydantic model 为单一定义源。FastAPI 自动产出 OpenAPI schema，用 `openapi-typescript` 生成前端类型，纳入 CI 或 pre-commit。

- 规则：**任何接口改动，先改 Pydantic model**，前端类型由生成器刷新，禁止手写接口类型
- 收益：类型不同步从「靠人记得」变成「构建时暴露」

### 代价二：流式多一跳

**缓解**：**不要让 Next.js 代理转发 SSE。** 浏览器直连 FastAPI 的流式端点（CORS + JWT）。

- 数据路径：`浏览器 → FastAPI → LLM`，Next.js 不参与
- 若必须经 Next.js 转发，务必关闭响应缓冲，否则逐字输出会退化成整段吐出
- 附带收益：用户关闭页面时，FastAPI 能直接感知客户端断开并取消上游调用

### 代价三：跨服务鉴权

**缓解**：前后端信任同一份 **Supabase JWT**。FastAPI 侧用 Supabase 的公钥校验签名，不自己签发 token。

- 收益：省掉自建认证与双份会话管理

### 代价四：跨进程调试

**缓解**：
- **契约先行**：先定 OpenAPI，前后端各自并行开发
- **trace_id 贯穿**：Next.js 生成请求 ID 并透传给 FastAPI，两边日志用同一 ID，Langfuse trace 也带上

### 代价五：双份部署

**缓解**：本地用 `docker compose` 一键拉起三层（web / api / postgres），开发阶段零部署成本。

- 上线时：前端 Vercel，后端 Fly.io / Railway，各自的 CI 独立

---

## 4. 此前设计的 Agent 架构如何落地

前面讨论过的编排设计，在 LangGraph 上的对应实现：

| 设计意图 | LangGraph 实现 |
|---|---|
| 确定性 workflow 骨架 | `StateGraph`：显式定义节点与边，不让模型决定流程 |
| 并行改写 N 段经历 | fan-out（`Send` API 或并行节点），fan-in 汇总 |
| 评分 → 修订循环 | 条件边（`conditional_edge`）实现判定与回环 |
| 最多 2 轮 + 分数阈值 + 成本上限 | 图状态中的计数器与守卫条件 |
| 中途失败可恢复 | Checkpointer（Postgres 后端） |
| 用户确认 / 精调介入 | `interrupt()` + 恢复 |
| 输出历史最佳版本 | Checkpointer 多版本快照 + 自定义选取节点 |

**注意**：评分者与生成者使用**不同厂商的模型**、评分时**隐藏轮次信息**（盲评），这两条是 LangGraph 之外的设计约束，需要在 prompt 与节点实现中落实。

---

## 5. 项目结构建议

> 以下为 **M0 落地后的实际结构**（2026-10-08 更新；原「建议稿」中的 `docs/requirements.md` 已移到仓库根目录）。

```
resume-optimizer/
├── apps/
│   ├── web/                      # Next.js 15：UI、认证、流式消费
│   │   ├── src/app/              # App Router
│   │   └── src/lib/              # env 与类型化 API 客户端
│   └── api/                      # FastAPI：业务逻辑、LangGraph 编排
│       ├── app/
│       │   ├── agents/           # 各 agent 与 graph 定义
│       │   ├── routers/          # HTTP 路由
│       │   ├── schemas/          # Pydantic model（类型单一定义源）
│       │   ├── services/         # 业务服务
│       │   ├── config.py         # 配置集中处
│       │   ├── db.py             # 数据访问层
│       │   └── tracing.py        # trace_id 贯穿
│       ├── scripts/              # OpenAPI 导出等运维脚本
│       └── tests/
├── packages/
│   └── api-types/                # openapi-typescript 生成产物
├── supabase/
│   ├── config.toml               # Supabase 本地实例
│   ├── migrations/               # 结构变更的唯一来源
│   └── seed.sql
├── scripts/                      # 根级工程脚本（Node，跨平台）
├── docs/
│   ├── tech-stack.md             # 本文
│   └── M0-summary.md             # M0 交付总结
├── requirements.md               # 需求分析（根目录）
├── tasks.md                      # 任务分解（根目录）
├── README.md
└── docker-compose.yml            # 一键拉起 web + api + postgres
```

---

## 6. 开发前必须落地的三条约定

1. **Pydantic model 是接口的单一定义源** —— 前端类型一律生成，不手写
2. **流式端点由浏览器直连后端** —— 不让 Next.js 参与数据转发
3. **前后端共用 Supabase JWT** —— 不重复造认证

这三条只要有一条破了，方案 A 的摩擦就会立刻回到「双语言项目最痛」的状态。
