# M0 交付总结 · 脚手架

> 2026-10-08 起，2026-10-09 补 M0-8｜覆盖 issue **M0-1 ~ M0-8（全部完成）**
> 配套文档：`../requirements.md`（需求）、`../tasks.md`（任务分解）、`tech-stack.md`（选型）

---

## 0. 一句话结论

三层服务（Next.js / FastAPI / Postgres）**加上一条真实可查的 LLM 观测链路**，骨架已经立起来并且可验证：
前端能真实调通后端接口、后端能真实读回数据库里由 migration 写入的数据、
**一次 LLM 调用能在自建的 Langfuse 实例里查到完整 trace**（trace → generation 两级结构）。

**但本机有一个未解决的环境前提**：pnpm 在这台 Windows 上无法完成依赖安装（详见第 7 节）。
这不影响代码正确性，但影响「clone 下来就能跑」这一步。

仓库已 `git init` 并按逻辑边界提交（见 §10）；除 `docker compose up --build` 的镜像层构建外，
M0-1 ~ M0-8 均已在本机实测通过。

---

## 1. 技术栈

| 层 | 选型 | 版本 | 职责 | 在 M0 里做了什么 |
|---|---|---|---|---|
| 前端 | Next.js（App Router） | 15.x | UI 渲染、流式消费 | 自检台页面，浏览器直连后端 |
| 前端语言 | TypeScript | 5.8 | 类型安全 | `strict` + `verbatimModuleSyntax` |
| 前端样式 | Tailwind CSS | 4.x | 样式 | 用 `@tailwindcss/postcss`，无 config 文件 |
| 后端 | FastAPI | ≥0.115 | **全部业务逻辑** | `/health`、`/api/v1/system/info` |
| 后端语言 | Python | 3.12+（本机跑 3.13） | — | `requires-python = ">=3.12"` |
| 依赖管理 | uv | 0.12.23 | 后端依赖 | `pyproject.toml` + `uv.lock` |
| 后端质量 | ruff / pytest | 0.7+ / 8.3+ | lint + 测试 | 9 条测试，含降级分支 |
| 数据库 | Postgres + pgvector | 16 | 业务数据、向量检索 | `service_meta` 表 + RLS |
| ~~数据库（托管）~~ | ~~Supabase~~ **未采用** | — | — | 2026-10-09：定位为本地自托管工具，数据层统一为纯本地 Postgres |
| 编排 | docker compose | — | 本地三层一键起 | web + api + postgres |
| Agent 编排 | LangGraph | — | StateGraph / Checkpointer | **M4 才接入**，M0 只留目录 |
| 观测 | Langfuse | v3（server） / 3.15（SDK） | trace / 评测 | ★ **M0-8**：自建实例 + SDK 接入，一次 LLM 调用可查 trace |
| LLM 调用 | httpx / OpenAI 兼容协议 | 0.27+ | 模型调用 | ★ **M0-8**：`stub` 与 `openai-compatible` 双 provider |
| 包管理 | pnpm workspace | 11.5.0 | monorepo | workspace + `pnpm-lock.yaml` |
| CI | GitHub Actions | — | lint / test / 类型同步 | 4 个 job |

**选型理由一句话**：前端只做壳，业务逻辑与 agent 编排全放 Python。
选 LangGraph 不是因为「更熟 Python」，而是它在**持久化编排**上的成熟度明显更高
（Checkpointer 中断恢复与多版本快照、`interrupt()` 人工介入、条件边直接表达评分循环、time-travel 回放）
—— 而这四项恰好是本项目 M4/M5/M6 的核心设计。

---

## 2. 项目文件架构

```
简历优化器/
│
├── README.md                       # 上手入口：快速开始 / 命令表 / 目录导览
├── requirements.md                 # 需求分析（原 docs/ 下，已移到根目录）
├── tasks.md                        # 9 个里程碑 / 57 个任务
├── package.json                    # 根脚本入口（dev / test / gen:types / db:up …）
├── pnpm-workspace.yaml             # workspace 声明：apps/* + packages/*
├── pnpm-lock.yaml                  # 锁文件（CI 用 --frozen-lockfile）
├── .npmrc                          # 记录了 Windows 下 pnpm 的前置条件（见第 7 节）
├── .python-version                 # 3.13
├── .gitignore / .editorconfig / .dockerignore
├── docker-compose.yml              # ★ M0-4：web + api + postgres 三层编排
│                                   #   ★ M0-8：+ Langfuse 六容器（observability profile）
│
├── .github/workflows/ci.yml        # 4 个 job：api / types-sync / web / compose
│
├── apps/
│   ├── web/                        # ★ M0-2：Next.js 15 前端（只做壳）
│   │   ├── Dockerfile              #   多阶段 + standalone 产物
│   │   ├── next.config.ts          #   output: standalone；Windows 轮询 watch
│   │   ├── postcss.config.mjs      #   Tailwind v4 插件
│   │   ├── tsconfig.json           #   @/* 与 @resume/api-types 的 paths 别名
│   │   ├── eslint.config.mjs       #   flat config
│   │   ├── .env.example            #   NEXT_PUBLIC_API_BASE_URL
│   │   ├── public/
│   │   └── src/
│   │       ├── app/
│   │       │   ├── layout.tsx      #   根布局 + metadata
│   │       │   ├── globals.css     #   Tailwind 入口 + 主题 CSS 变量
│   │       │   └── page.tsx        #   ★ M0-7：M0 自检台（三段链路 + 读库结果）
│   │       └── lib/
│   │           ├── env.ts          #   运行时配置（后端基址）
│   │           └── api-client.ts   #   类型化客户端 + trace_id 生成
│   │
│   └── api/                        # ★ M0-3：FastAPI 后端（全部业务逻辑）
│       ├── Dockerfile              #   基于 uv 官方镜像，依赖层单独缓存
│       ├── pyproject.toml          #   ★ 依赖 + ruff + pytest 配置一处集中
│       ├── uv.lock                 #   锁文件
│       ├── .env.example
│       ├── scripts/
│       │   └── export_openapi.py   #   ★ M0-6：直接 import app 导出 schema，不需起服务
│       ├── app/
│       │   ├── main.py             #   应用装配：中间件 → 路由 → 生命周期
│       │   ├── config.py           #   ★ 配置集中处（pydantic-settings）
│       │   ├── db.py               #   数据访问层（惰性连接池，失败降级不崩）
│       │   ├── tracing.py          #   ★ trace_id 贯穿（前端 ID 原样回写）
│       │   ├── runtime.py          #   进程运行时信息（uptime / utcnow）
│       │   ├── deps.py             #   FastAPI 依赖别名
│       │   ├── llm/                #   ★ M0-8：LLM 调用抽象
│       │   │   └── client.py       #     stub / openai-compatible 双 provider
│       │   ├── observability/      #   ★ M0-8：观测（Langfuse 包装）
│       │   │   └── langfuse_client.py  #  生命周期、降级策略、trace_id 规范化
│       │   ├── schemas/            #   ★ 接口类型的**单一定义源**
│       │   │   ├── health.py       #     HealthResponse / SystemInfoResponse …
│       │   │   ├── meta.py         #     ErrorResponse
│       │   │   └── observability.py #    M0-8：status / smoke 的请求响应体
│       │   ├── routers/
│       │   │   ├── health.py       #     /health（无前缀，给容器 healthcheck）
│       │   │   ├── system.py       #     /api/v1/system/info（故意读库）
│       │   │   └── observability.py #    M0-8：/observability/status、/smoke
│       │   ├── services/           #   业务服务（M1 起填充）
│       │   └── agents/             #   LangGraph graph（M4 起填充）
│       └── tests/
│           ├── conftest.py         #   假数据库替身 + 与本机 .env 隔离
│           ├── test_health.py      #   6 条：200 / 前缀 / 不碰库 / trace_id / schema
│           ├── test_system.py      #   2 条：读库成功 / 数据库不可用时降级
│           ├── test_observability.py  # 11 条：trace_id 规范化 / 降级 / smoke 端到端
│           └── test_config.py      #   4 条：CORS 逗号写法回归（见 §7.6）
│
├── packages/
│   └── api-types/                  # ★ M0-6：OpenAPI → TS 类型产物
│       ├── package.json            #   openapi-typescript
│       ├── tsconfig.json
│       └── src/
│           ├── index.ts            #   纯转发，不放任何手写类型
│           └── schema.d.ts         #   **生成产物但入库**（理由见 §4.1）
│
├── supabase/                       # ★ M0-5：数据库结构变更的唯一来源（纯 SQL，无需 Supabase）
│   ├── migrations/
│   │   └── 20261008000000_init.sql #   扩展 + service_meta + RLS
│   └── seed.sql                    #   幂等种子数据
│
├── scripts/                        # 根级工程脚本（Node，跨平台）
│   ├── api-setup.mjs               #   在 scripts/.tools/uv 里装 uv，再 uv sync
│   ├── run-api.mjs                 #   统一用 apps/api/.venv 跑 pytest / ruff / uvicorn
│   ├── gen-api-types.mjs           #   ★ M0-6 管线，支持 --check 供 CI 用
│   ├── verify-m0.mjs               #   M0 验收脚本（逐条判定 PASS/FAIL/SKIP）
│   ├── lib/{proc,paths}.mjs        #   跨平台子进程与路径工具
│   ├── issues.json                 #   66 个 issue 的定义（#1–#66）
│   └── create_issues.py            #   批量建 issue
│
└── docs/
    ├── tech-stack.md               # 选型定案（结构树已按实际更新）
    └── M0-summary.md               # 本文
```

---

## 3. 运行时拓扑

```
                     ┌──────────────────────────────┐
   浏览器  ──────────►│  Next.js 15   :3000          │
        ▲            │  只做 UI 渲染 / 流式消费      │
        │            └──────────────────────────────┘
        │
        │  ② 浏览器**直连**后端取数据（不经 Next.js 转发）
        ▼
   ┌────────────────────────────┐
   │  FastAPI      :8000        │  /health
   │  业务逻辑 + agent 编排      │  /api/v1/system/info
   │  TraceIdMiddleware         │  /openapi.json
   └───────────┬────────────────┘
               │ asyncpg 连接池（惰性建立）
               ▼
   ┌────────────────────────────┐
   │  Postgres 16 + pgvector    │  :54322（宿主）
   │  service_meta（migration）  │  :5432 （容器内）
   └────────────────────────────┘

                     ┌──────────────────────────────────────┐
   FastAPI ─────────►│  Langfuse v3   :3300（宿主）          │
   Langfuse SDK      │  web + worker + clickhouse + redis    │
   （OTLP 批量上报）  │  + minio + postgres（独立一套）        │
                     └──────────────────────────────────────┘
```

**刻意的一点**：Langfuse 的六个容器挂在 compose 的 `observability` profile 下，
常规 `docker compose up` **不会**拉起它们 ——
日常开发只想跑业务链路时不必背上一个 ClickHouse。
需要看 trace 时按需起：`docker compose --profile observability up -d`。

**刻意的两点**

- 浏览器直连后端，而不是让 Next.js 代理转发。这是 `tech-stack.md` 代价二的缓解措施：
  流式端点若经 Next.js 转发，必须关掉响应缓冲，否则逐字输出会退化成整段吐出。
  自检页上那句「客户端耗时 … ms · 浏览器直连后端」就是在展示这条路径真的生效。
- 端口选 54322 而不是 5432：避开本机可能已有的 Postgres 实例，也让「这个库属于本项目」一眼可辨。
  （2026-10-09 注：当时选这个值还有「与 Supabase 本地实例对齐」的考虑，该考虑已随定位变更作废。）

---

## 4. M0 关键设计决策

这些是 M0 阶段定下、后续里程碑会一直受影响的约定。

### 4.1 类型管线：Pydantic 是单一定义源，且**生成产物入库**

```
apps/api/app/schemas/*.py  ──FastAPI──►  openapi.json  ──openapi-typescript──►  schema.d.ts
      单一定义源                          不入库              生成产物 → 入库
```

- `openapi.json` 不入库（纯中间产物）
- `schema.d.ts` **入库**，反常但有理由：
  1. 前端可以脱离 Python 环境构建（clone 下来 `install && build` 即可）
  2. Vercel 构建不必装 uv 与 Python，少一项构建依赖就少一个失败点
  3. 前后端可并行开发
- 同步性由 CI 的 `pnpm gen:types --check` + `git diff --exit-code` 兜住：
  有人改了 Pydantic 却没提交刷新后的类型，CI 直接红。

**导出 schema 用 `import app` 而不是「起服务再 curl」**：CI 少一步拉容器，
且拿到的 schema 与待部署代码 100% 同源，不必处理端口占用与启动超时。

### 4.2 数据库连接是惰性的，且故障必须降级而非崩溃

`db.py` 不在应用启动时建池，第一次真正用到才建；建池失败返回 `None`，由调用方降级。

- 若在 lifespan 里连库，`docker compose up` 时 postgres 慢启动会把 api 拖死
- 因此 `/health` **不访问数据库**，永远返回 200 —— 这是它的语义：进程活着就 200
- `/api/v1/system/info` 才读库，连不上时返回 `database.connected = false` 而**不是 500**，
  自检页据此显示「后端在线 · 数据库未通」，能一眼看出是哪一段断了
- 测试里用 `dependency_overrides` 把 `get_database` 换成替身，
  于是「库通了」和「库没通」两条分支都能被确定性地覆盖，CI 不必起 postgres

> 顺带：池失效时（例如 postgres 被重建）会丢弃旧池以便下次重连。

### 4.3 `/system/info` 故意读库 —— 一个接口同时证明三件事

它返回的 `meta` 来自 migration 创建的 `service_meta` 表。前端能把它渲染出来，等于同时证明：

1. 前端 → 后端 通
2. 后端 → 数据库 通
3. migration 真的作用到了这个库上

这比「接口返回 200」有力得多，也是 `verify-m0.mjs` 能自动判定 M0-5 与 M0-7 的基础。

### 4.4 trace_id 中间件（M8-1 的地基）

前端生成 `X-Request-Id` 下发 → 后端原样回写响应头 → 同时写进访问日志。
后续 M8-1 直接把它当作 Langfuse trace 的标识，就实现了「按一个 ID 串起前端、后端、LLM 调用」。
未带该头时后端自行生成 `uuid4().hex`。

### 4.5 supabase/migrations 是结构变更的唯一来源（含一处已知取舍）

> 2026-10-09 注：目录名沿用 `supabase/`，但它现在只是「Supabase 兼容布局的纯 SQL 目录」——
> 运行时不依赖 Supabase，也不需要它的 CLI（原本唯一的 Supabase CLI 配置 `config.toml` 已删除）。

- `supabase/migrations/20261008000000_init.sql` 是权威定义，`pnpm db:reset` 会重放它
- 为了让 `docker compose up` 也能一键得到正确的库，compose 把该文件与 `seed.sql`
  **逐个文件**挂进 `docker-entrypoint-initdb.d`

**取舍要说清楚**：`docker-entrypoint-initdb.d` 只在数据目录为空时执行，且**不递归子目录**，
所以每新增一个 migration 都要在 compose 里加一行挂载。这是有意接受的短期成本 ——
将来若要引入单一迁移执行器（alembic 等），只需把它指向同一批 SQL 文件，不存在两份 schema 定义。

另外 migration 里刻意**没有**写 `to anon, authenticated`：本项目的库是原生 postgres / pgvector 镜像，
这两个角色并不存在，写上会让 migration 在 compose 里跑不起来。用默认的 PUBLIC 兜底。

### 4.6 `NEXT_PUBLIC_*` 是构建期常量

`docker-compose.yml` 里 web 服务把它作为 **build args** 传入，而不是运行时 `environment`。
写错成运行时环境变量不会报错，只会静默地把 `undefined` 打进客户端 bundle —— 这类问题排查成本很高，
所以在 compose 里写了注释钉死。

### 4.7 纯类型包不做 workspace 依赖，改用 tsconfig 别名

`@resume/api-types` 只有 `.d.ts` 与 `export type`，前端一律 `import type`，编译期即被擦除。
因此它**不需要**是 workspace dependency，也不必 `transpilePackages` ——
用 tsconfig 的 `paths` 别名引用即可，少一条依赖就少一处链接与版本对齐的麻烦。
（这条在本次排障中意外成了关键，见第 7 节。）

### 4.8 观测是横切能力，三条硬约束（M0-8）

`observability/langfuse_client.py` 定下三条，后续里程碑一直受用：

1. **可降级**。观测不该成为可用性的单点。未配置 key 时 SDK 干脆不初始化，
   `span()` / `generation()` 退化成 no-op（`yield None`），业务链路照常跑完。
   连 SDK 初始化抛异常都被吞掉 —— 「观测挂了不能把服务带下水」。
2. **trace_id 复用请求 ID**。`TraceIdMiddleware` 已为每个请求确定 `X-Request-Id`，
   这里把它规范化成 32 位小写十六进制后当作 Langfuse 的 trace_id。
   于是「前端日志 → 后端日志 → LLM 调用」共用一个 ID，M8-1 直接按 ID 回放整条链路。
   规范化规则：带横线的 UUID **去掉横线正好 32 位**（最理想），其余取 md5（确定性，便于反查）。
3. **不向外暴露 SDK**。业务代码只见 `get_observability()` 与 `span()` / `generation()`，
   换观测后端只改这一个文件。

**为什么 smoke 接口要真跑一次 LLM 调用（哪怕 provider 是 stub）**：
只有真调用才能证明 `SDK → 上报 → ClickHouse → UI` 这条链路是通的。
伪造一条 trace 只能证明「我调用了 SDK 的 API」。

### 4.9 LLM 抽象：`stub` 与 `openai-compatible` 双 provider（M0-8）

M0-8 的验收标准是「一次 LLM 调用能在 Langfuse 里看到 trace」，
但 CI 与无 key 环境不能去调真实模型，所以把 LLM 也抽象成一层：

- `stub`：**明确标注的假实现**（输出前缀 `【stub】`、`is_stub=True`、不发任何网络请求），
  但**照样产生真实的 trace 与 token 统计** —— 它验证的是观测链路，不是模型能力
- `openai-compatible`：`httpx` POST 到 `{base_url}/chat/completions`，
  DeepSeek / Moonshot / 通义 / vLLM / Ollama 全走同一套协议，换模型只改配置

配套接口（都不是业务接口，只为回答「观测配好了吗」「配好了真有 trace 吗」）：
- `GET /api/v1/observability/status` —— key 是否配齐、实例是否可达、是不是 stub
- `POST /api/v1/observability/smoke` —— 跑一次被完整 trace 的调用并返回 trace URL

`langfuse_reachable` 未配置时返回 `null` 而不是 `false`：**null 是「没开」，false 是「开了但连不上」**，
两者含义不同，混成一个布尔值会让排障时误判。

---

## 5. M0 验收结果

验收脚本：`pnpm verify:m0`（= `node scripts/verify-m0.mjs`），逐条判定 PASS / FAIL / SKIP。
最近一次实跑：**9 通过 / 0 失败 / 0 跳过**（后端与 postgres 在线时可验到运行态）。

脚本对 `M0-2` 做了三级降级（本地 `tsc` 二进制 → `pnpm` → `npm`），
所以在「pnpm 不可用」的本机上也能得出与代码相关的结论，而不是把环境问题记成代码失败。

| 编号 | 验收标准（原文） | 结果 | 证据 |
|---|---|---|---|
| M0-1 | 各子项目可独立启动 | PASS | `apps/web`、`apps/api`、`packages/api-types`、`docs`、`supabase/`、compose、workspace 声明齐全 |
| M0-2 | `pnpm dev` 正常 | PASS（以 npm 等价验证） | `apps/web` 依赖装齐；`npm run build` 通过：`✓ Compiled successfully` → `✓ Generating static pages (4/4)`，`/` 预渲染为静态，First Load JS 106 kB。`pnpm` 本身在本机不可用，见 §7.1 |
| M0-3 | `/health` 返回 200 | PASS | pytest 8/8 通过；`/health` 与 `/api/v1/health` 均 200，且断言了「不访问数据库」 |
| M0-4 | `docker compose up` 一键全起 | 部分 | `docker compose config --quiet` 校验通过（exit 0）；镜像层构建受本机网速限制，见 §7.3 |
| M0-5 | 能连库且 migration 可执行 | PASS | 真实 postgres 容器内 migration 已生效：`service_meta` 4 行、`vector`+`pgcrypto` 已装、RLS 开启；`/api/v1/system/info` 读回 `service_meta`，`database.connected=true`、latency 2.96 ms |
| M0-6 | 改 Pydantic 后前端类型自动刷新 | PASS | 实际改过 Pydantic（见 §7.4）并重新生成成功；`gen-api-types.mjs --check` 无漂移；CI 有独立 job 拦截 |
| M0-7 | 前端页面显示后端返回的数据 | PASS | 自检台展示 `service_meta` 内容 + trace_id + 客户端耗时；CORS 预检自 `localhost:3000` 通过 |
| M0-8 | Langfuse 本地实例 + SDK 接入；一次 LLM 调用能看到 trace | PASS | Langfuse v3 六容器本地起齐（web/worker/clickhouse/redis/minio/postgres）；`POST /observability/smoke` 落 trace：trace `m0-8-smoke`（`cd12e8b2…`）→ GENERATION `llm.complete`，model `deepseek-chat`，usage `{input:9, output:27}`。接口回传 `langfuse_trace_url` 可直接点开 |

> 除 M0-4 的镜像构建外，各项均在本机实测通过。M0-4 的「部分」是网速问题，不是代码问题，说明见 §7.3。
> M0-8 的 trace 是**从 Langfuse 公共 API 反查确认**的，不是「接口返回 200 就算过」。

---

## 6. 代码层面已固化的保障

| 保障 | 手段 |
|---|---|
| 后端 lint / 测试 | `ruff check`（E/F/W/I/UP/B/C4/SIM/ASYNC/RUF）+ 24 条 pytest |
| 测试不依赖真库 | 依赖覆盖 + 假数据库替身 |
| 测试不依赖本机 `.env` | `_isolate_settings` 夹具把 `env_file` 摘掉并清 `lru_cache`（见 §7.5） |
| 前后端类型不漂移 | `gen:types --check` + CI 独立 job |
| 编排配置正确性 | CI 跑 `docker compose config --quiet` |
| 前端可独立构建 | `schema.d.ts` 入库，前端不需要 Python |
| 观测故障不致命 | key 缺失 / SDK 初始化失败 / flush 失败全部降级为 no-op，不抛业务异常 |
| 依赖隔离 | CI 以 `npm_config_node_linker=isolated` 覆盖本地 workaround，幻影依赖在 CI 被拦下 |

---

## 7. 本次踩到的问题（含未解决项）

### 7.1 【未解决 · 阻塞本机开发】pnpm 无法完成依赖安装

**现象**：`pnpm install` 报

```
[UNKNOWN] UNKNOWN: unknown error, symlink '..\..\@types+node@22.20.5\node_modules\@types\node' -> '...'
at async Object.symlink (node:internal/fs/promises:1001:10)
```

或直接**在链接阶段挂死**（包已下完，卡在 `added 1` 十分钟无进展）。
`node_modules` 只落地 `.pnpm` 一个目录。

**根因**（读 pnpm 11.5.0 源码 `pnpm.mjs:77321` 确认）：

```js
// pnpm 内置 symlink-dir 在 Windows 上的策略
createSymlinkAsync = async (target, path) => {
  try {
    await createTrueSymlinkAsync(target, path);   // fs.symlink(相对路径, link, "dir")
  } catch (err) {
    if (err.code === "EPERM") {                   // ← 只认 EPERM
      await createJunctionAsync(target, path);    //   才降级为 junction
    } else {
      throw err;                                  // ← 本机抛 UNKNOWN(-4094)，直接失败
    }
  }
};
```

**已排除的假设**（都用实验否定掉了，不是猜的）：

| 假设 | 验证方式 | 结论 |
|---|---|---|
| 中文路径导致 | Node 在中文路径下 mkdir/symlink 全部成功 | 排除。日志里的 `闈㈣瘯浼樺寲鍣╘` 只是 PowerShell 5.1 把 UTF-8 按 GBK 解码的**显示假象**，磁盘上没有乱码目录 |
| 文件系统不支持符号链接 | 四个盘全为 NTFS；`fsutil behavior query SymlinkEvaluation` 本地到本地「已启用」 | 排除 |
| 缺权限 | 同目录下 `fs.symlink(..., "junction")` 成功，只有 `"dir"` 失败 | 部分成立：`"dir"` 需要开发者模式/管理员，但**决定性原因是 pnpm 只对 EPERM 降级**，对 UNKNOWN 不降级 |
| pnpm 配置能绕开 | 试过 `node-linker=hoisted`、再叠加 `hoist=false`（后者反而挂死） | pnpm 无任何配置可强制走 junction |

**解决（二选一，做一次即可）**：
- A. 设置 → 系统 → 开发者选项 → 打开「**开发人员模式**」
- B. 以管理员身份运行一次 `pnpm install`

做完后应删掉 `.npmrc` 中的说明性备注即可（当前文件里已不再设置任何绕行参数）。

**在满足前提之前的替代路径**：用 npm 安装（npm 在 Windows 上走 junction，不受影响）：

```bash
cd apps/web && npm install
cd packages/api-types && npm install
```

这不改变包管理器选型，CI 仍以 pnpm + `pnpm-lock.yaml` 为准。

### 7.2 【已绕过】网络对 Python 生态极慢

- `pip install uv` 从 pypi.org 实测 **21.6 kB/s**，18MB 的 wheel 下了 16 分钟
- 清华源未收录 `uv`（`from versions: none`）；阿里云源可用（71 kB/s）
- 最终做法：`Invoke-RestMethod https://pypi.org/pypi/uv/json` 取 wheel URL → 直接下载 → `pip install <本地 wheel> --no-index`
- uv 侧改用 `UV_DEFAULT_INDEX=https://registry.npmmirror.com/pypi/simple`，并显式 `UV_PYTHON` 指向已装的 3.13，**避免去 GitHub 拉 CPython 3.12**
- 因此 `.python-version` 写的是 **3.13**，而 `pyproject.toml` 的 `requires-python` 仍是 `">=3.12"` ——
  文档里写的「Python 3.12」是目标下限，本机实际用 3.13（满足约束）。此处以 `requires-python` 为准。

### 7.3 【已记录】`docker compose up` 首次构建慢

- 本机拉 `pgvector/pgvector:pg16` 镜像速度有限，首次约需较长时间
- 日常开发推荐只起基础设施：`pnpm db:up`（只起 postgres），前端与后端跑在宿主机上，热更新最快
- 需要验证「一键全起」时再 `pnpm stack:up`

### 7.4 【已修复】构建揪出「契约与类型不一致」

首次 `npm run build` 在 type-check 阶段失败：

```
./src/app/page.tsx:130:18
Type error: 'd.meta' is possibly 'undefined'.
```

根因不在前端，也不在生成器，而在**后端契约表达得不准确**：
`SystemInfoResponse.meta` 原本写成 `Field(default_factory=list, ...)`，
Pydantic 会把带默认值的字段排除出 OpenAPI `required`，于是生成出来的是 `meta?: ...`，
前端就得给每个调用点加 `?? []`。

但真实契约是「**永远存在，数据库不可用时为空数组**」——路由里也确实是 `meta=meta` 无条件传的。
所以正确修法是让后端声明它为必填（去掉 `default_factory`），重新生成类型，
`schema.d.ts` 里 `meta` 变回 `meta:`，前端不用加任何兜底：

```diff
- meta: list[ServiceMetaEntry] = Field(default_factory=list, description="...")
+ meta: list[ServiceMetaEntry] = Field(description="...")
```

**这条比 §7.1 更适合讲工程判断**：面对类型报错，直觉是「前端加个 `?? []` 就好」，
但那样等于让前端替后端圆谎，契约会长期偏软。沿着「谁定义了真相」往回找，
问题落在 Pydantic 的必填语义上，改一处即可。

> 顺带证明了类型管线的价值：这个不一致是**构建阶段**被拦下的，没机会流到运行期。

### 7.5 【已修复】测试偷偷依赖了开发者本机的 `.env`

M0-8 之前 `apps/api/.env` **压根不存在**，测试跑的是全部默认值，于是「测试干净」是一种假象。
一旦为 M0-8 建了 `.env`（填上 langfuse key），两条原本通过的用例立刻变红：

```
assert payload["langfuse_configured"] is False
E   assert True is False
```

根因：`Settings` 的 `env_file=".env"` 是**相对当前工作目录**解析的，
从 `apps/api` 跑 pytest 就会被读进来。也就是说「同一份代码，我这儿过、CI 不过」。

**修法**（`tests/conftest.py` 的 `_isolate_settings` 自动夹具）：
把 `Settings.model_config["env_file"]` 置为 `None`（pydantic-settings 是**实例化时**才从
`cls.model_config` 取这一项，所以运行期改写有效），并清掉 `get_settings` / `get_observability` 的 `lru_cache`
—— 后者是必须的：`app.main` 在 import 时就 `create_app()` 过一次，已经把「带本机 key 的 settings」缓存住了。

### 7.6 【已修复】`CORS_ORIGINS=a,b` 直接把服务打不起来

同一个 `.env` 又暴露一个更早埋下的坑。`config.py` 里明明写了 `field_validator` 处理逗号分隔，
但启动直接抛：

```
pydantic_settings.exceptions.SettingsError: error parsing value for field
"cors_origins" from source "DotEnvSettingsSource"
caused by: json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
```

**根因**：pydantic-settings 把 `list[str]` 当成「复杂类型」，
在**读取 source 阶段**就抢跑 `json.loads`，此时字段校验器**还没有机会执行**。
于是校验器写得再对也没用 —— 而 `.env.example` 里推荐的正是逗号写法，文档与实现对不上。

**修法**：给字段加 `NoDecode`，让原始字符串原样交给校验器自己切分：

```python
cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=...)
```

同时让校验器兼容两种写法（逗号分隔 / JSON 数组），并补 `tests/test_config.py` 四条用例锁住行为。

> 这条比 §7.4 更隐蔽：它不是「写错了」，是「框架的优先级与人的直觉不一致」。

### 7.7 【已修复】FastAPI 0.142 的原生 OTel 把 trace 冲成噪声（★ 值得讲）

M0-8 接上 Langfuse 后，Langfuse 里出现了大量**没人写过**的 trace：

```
GET /api/v1/health      OPTIONS
GET /api/v1/system/info GET /api/v1/health ...
```

看一眼 span 的 `scope.name` 是 `fastapi`，子 span 是 `fastapi.endpoint` / `fastapi.dependencies` /
`fastapi.serialization` —— 每个请求都会生成一条。

**根因**：FastAPI 0.142 起自带原生 OpenTelemetry 埋点，
一旦检测到全局 `TracerProvider`（Langfuse SDK 会设置它）就自动给每个请求开 trace。
我们精心命名的业务 trace 因此被冲散。

**修法**：显式关掉 FastAPI 的原生埋点，观测统一由 Langfuse SDK 负责：

```python
app = FastAPI(..., telemetry={"tracing": False, "metrics": False,
                              "logs": False, "auto_configure": False})
```

**验证方式（关键是这个）**：主动打 12 个请求（含 `Origin` 头以触发 CORS 预检），
再查 Langfuse 公共 API 的 `totalItems`，**前后 delta = 0** —— 零新增 trace，证明埋点确实关干净了。
而不是「看日志里没有报错」。

### 7.8 【已修复】SDK 的 `get_trace_url()` 在本地实例上永远返回 404 前的 308

`observability.trace_url()` 一开始直接委托给 SDK 的 `get_trace_url()`，结果始终是 `None`。
抓到的异常是：

```
ApiError status_code: 308   # /api/public/projects
```

SDK 内部会先调 `self.api.projects.get()` 拉项目列表来解析 baseUrl，
而本地实例对 `/api/public/projects` 返回 308 重定向，SDK 的 httpx 客户端不跟随 → 直接抛错。

**修法**：项目 ID 是启动时由 `LANGFUSE_INIT_PROJECT_ID` 固定下来的，直接手工拼更稳：

```python
f"{langfuse_host}/project/{langfuse_project_id}/traces/{trace_id}"
```

修完后 `langfuse_trace_url` 稳定返回可点开的详情页地址。

### 7.9 【已修复】langfuse 3.15 的 `start_as_current_generation` 已废弃

同样是被「有了真 key 才会走到」的代码路径暴露出来的：`start_as_current_generation`
在 3.15 会发 `DeprecationWarning`，而本项目把告警当错误处理，一调用就炸。
改用统一入口 `start_as_current_observation(as_type="generation")`；
`span` 也一并换成同一入口（`as_type="span"`），避免代码里两种风格并存。

> 7.5 ~ 7.9 有个共同点值得说：它们**全部**是「第一次填上真实配置」才浮现的。
> 之前一路绿灯，是因为所有分支都恰好在走默认值/no-op 路径。

---

## 8. 面试要点

### 8.1 先想清楚：这个项目到底在讲什么

它**不是**「用 LLM 改简历」这种一句话能说完的工具，而是三个可展开的工程命题：

1. **双语言 monorepo 的类型一致性怎么保证**（方案 A 的代价与缓解）
2. **不确定的 LLM 输出怎么变成可靠的产品流程**（垂直切片、评分循环、异构盲评、失败隔离）
3. **一个会持续迭代的 agent pipeline 怎么被观测和度量**（golden set、prompt 版本、成本监控）

第 1 条 M0 已经做完并可以现场演示；第 2、3 条是 M1/M4/M5/M8 的主体。

### 8.2 M0 阶段可以直接讲的点

| # | 讲什么 | 一句话钩子 | 可能的追问 & 怎么接 |
|---|---|---|---|
| 1 | **前端壳 + Python 全包的选型** | 「我没有选全栈 TS，因为 LangGraph 在持久化编排上的成熟度是实打实的收益，不是妥协」 | 「那你付出了什么代价？」→ 答五项代价与缓解措施，尤其类型双写与流式多一跳 |
| 2 | **类型单一定义源 + 生成产物入库** | 「Pydantic 是唯一真相，前端类型是算出来的。有个反常识的地方：生成产物我故意提交进仓库了」 | 「生成产物为什么入库？」→ 前端脱离 Python 环境可构建、Vercel 少了 Python 依赖、前后端可并行；同步性由 CI 的 diff 校验兜住 |
| 3 | **数据库惰性连接 + 故障降级** | 「/health 故意不碰数据库 —— 进程活着就该返回 200，把」 | 「那数据库挂了你怎么知道？」→ `/system/info` 降级返回 connected=false 而非 500，自检页能一眼看出断在哪一段 |
| 4 | **用一个接口证明三件事** | 「`/system/info` 返回的数据来自 migration 写的表，所以能渲染出来就等于同时证明了链路通、读库通、migration 生效」 | 这是「怎么设计可验证性」的好例子，比「接口返回 200」有力 |
| 5 | **trace_id 从第一天就贯穿** | 「前端生成请求 ID 下发，后端原样回写并写进日志，M8 接 Langfuse 时直接拿它当 trace 标识」 | 「为什么不直接用 Langfuse 的 SDK 自动生成？」→ 跨进程链路需要一个两端都知道的 ID |
| 6 | **pnpm 的 Windows 符号链接坑**（★ 最出彩） | 「我遇到的 pnpm 安装失败，最后是读它源码定位的：它只在抛 EPERM 时才降级用 junction，而我这台机器抛的是 UNKNOWN，降级分支永远走不到」 | 这是**真正能体现排查功力**的素材。可展开：先证伪中文路径、再证伪文件系统与权限、最后读源码定位到降级判断过窄。量化结论：`fs.symlink(dir)` 失败 / `junction` 成功 |
| 7 | **垂直切片优先的推进方式** | 「我没有先把素材库做完再做生成，而是先打通一条最小但完整的链路：一条经历 → 一份 PDF」 | 「为什么？」→ 集成风险永远大于模块风险，垂直切片最早暴露「PDF 导出对不上预览」这类致命问题 |
| 8 | **不在生命周期里连数据库** | 「如果在 lifespan 里建连接池，`docker compose up` 时 postgres 慢启动会把 api 拖死」 | 属于「你踩过部署的坑」的证据 |
| 9 | **构建揪出的类型契约 bug**（★ 见 §7.4） | 「构建报 `d.meta` 可能是 undefined。最省事的改法是前端加 `?? []`，但我把问题退回到后端：这个字段的契约本来就是『永远存在』，是我在 Pydantic 里给了默认值，才让 OpenAPI 把它标成非必填」 | 能体现「顺着单一定义源往回找，而不是在出错的地方打补丁」的判断力。可追问「类型系统在这里帮你拦住了什么」→ 拦住了前后端契约的长期软化 |
| 10 | **观测是横切能力，必须可降级**（★ 见 §4.8） | 「Langfuse 是观测，不该成为可用性的单点。没配 key 时 SDK 根本不初始化，`span()` 退化成 no-op，业务链路照常跑完」 | 「那你怎么知道观测挂了？」→ `/observability/status` 用 `null` / `false` 区分「没开」和「开了但连不上」 |
| 11 | **trace_id 从第一天就贯穿，现在兑现了**（见 §4.8 / §8.2-5） | 「M0 埋的 `X-Request-Id` 在 M0-8 直接用上了：前端生成的请求 ID 就是 Langfuse 的 trace_id，于是前端日志、后端日志、LLM 调用共用一个 ID，按它就能回放整条链路」 | 「UUID 和 trace_id 格式对不上怎么办？」→ `crypto.randomUUID()` 去掉横线正好 32 位十六进制；不是这个形状的输入退化为 md5（确定性，便于反查） |
| 12 | **FastAPI 0.142 原生 OTel 把 trace 冲成噪声**（★ 最出彩，见 §7.7） | 「接上 Langfuse 后冒出一堆我没写过的 trace，每个请求一条。根因是 FastAPI 新版自带原生 OTel 埋点，检测到全局 `TracerProvider` 就自动给每个请求开 trace —— 而 Langfuse SDK 恰好会设置它」 | 「你怎么确认真的关掉了？」→ 主动打 12 个请求再查 Langfuse 公共 API 的 `totalItems`，**前后 delta = 0**。这比「日志没报错」有力得多 |
| 13 | **框架优先级与直觉不一致**（见 §7.6） | 「`.env` 里写 `CORS_ORIGINS=a,b` 直接把服务起不来。校验器我明明写了，但 pydantic-settings 把 `list[str]` 当复杂类型，**在 source 阶段就抢跑 `json.loads`，校验器根本没机会执行**」 | 「怎么发现的？」→ 建真实 `.env` 才暴露；修法是用 `NoDecode` 显式声明「别替我解码」。可延伸：7.5~7.9 五个坑**全部**是「第一次填上真实配置」才浮现的 |

### 8.3 后续里程碑里最值钱的三个点（M0 之后要往这几处做）

1. **M4-3 并行改写的量化收益** —— 上下文隔离省了多少 token、fan-out 把 P95 延迟从 N×T 压到约 T。
   一定要留实测数字，不要只说「更快」。
2. **M5-3 异构模型 + 盲评** —— 这是 LLM-as-judge 的核心防御。
   同源评分会偏袒自己的输出；评分者知道「这是修改后的版本」时会倾向给高分。
   换厂商 + 隐藏版本信息可规避。能说清这个，说明真的做过 agent 评估而不是「调过 API」。
3. **M8-2 golden set** —— 90% 的同类项目没有评测集。`tasks.md` 里明确标注了这是「简历里最值钱的一条」。

### 8.4 面试时必须坦白的地方（不要说满）

> 下表写于 M0 交付时。2026-10-09 晚已按现状补注（M1 已完成、Supabase 未采用、平台形态已定）。

| 事实 | 怎么讲 |
|---|---|
| **M2 起（素材库、RAG、评分循环）尚未开始** | M0 只做到「骨架 + 一条被 trace 的 LLM 调用」（M1 的垂直切片已于 2026-10-09 完成）。不要含糊成「已完成端到端」。**可以说的是**：M0-8 的 trace 是真在自建 Langfuse 里查到过的，不是只调了 SDK 的 API |
| **M0-8 用的 LLM 是 `stub`（假实现）** | 明确说清楚：stub 会标记 `is_stub=True`、输出带 `【stub】` 前缀、不发网络请求。它验证的是观测链路，**不是**模型能力。真实调用走 `openai-compatible` provider，已实现但本机未配 key |
| **本机 pnpm 不可用** | 这是**加分项**而不是减分项：说明你知道自己环境的边界，并且定位到了上游源码。但要能说清「在正常环境上是可用的」 |
| **`docker compose up` 未完整跑通** | 受本机网速限制，只校验了配置。不要声称「一键全起已验证」（Langfuse 那套六容器是**实测起齐**的，可以讲） |
| **数据层曾考虑 Supabase，最终未采用** | 2026-10-09 定位改为本地自托管开源工具后，Supabase（Auth / RLS / 云库）整体移除，统一为纯本地 Postgres。这本身是个可讲的**决策收敛**过程：先按最坏情况留了演化口子，确定用不上后干净砍掉，不留半吊子依赖 |
| **平台形态已定：电脑端本机** | 2026-10-09 随「不上线」一并拍板 —— 本地自托管工具只能在电脑上跑，移动端适配放弃（见 `../requirements.md` §5.1） |

### 8.5 一个容易被追问的产品问题

**「BOSS 直聘是移动端产品，你这是个 Web 工具，中间『截图传电脑再传回来』的链路足以流失大部分用户。」**

这是本项目最大的产品风险（`requirements.md` §5.1），面试官如果懂行大概率会问。
不要回避，正面接：三种解法（响应式移动端 / 只做电脑端精修定位 / 浏览器插件抓 JD），
以及各自的代价。**承认这是个未决问题，比硬说「我做了移动端适配」诚实得多，也更能体现产品判断力。**

---

## 9. 遗留与下一步

### 立即可做（收尾 M0）
- [ ] 开启 Windows 开发者模式，然后 `pnpm install` 完整跑一遍，确认 `M0-2` 的**原验收路径**（`M0-2` 本身已用 `npm run build` 等价验证通过）
- [ ] `docker compose up --build` 完整验证 `M0-4`（首次拉镜像慢，建议挂后台）
- [x] `git init` 并逐步提交（**已完成，见文末「提交历史」**）
- [ ] 仓库与本地目录改名：`Interview-optimization` → `Resume-optimization`、`面试优化器` → `简历优化器`
- [ ] 推送到远端（`git remote add origin` + `push`），当前只有本地仓库

### M0-8（已完成）
- [x] Langfuse 本地实例（v3 六容器）+ SDK 接入，验收「一次 LLM 调用能看到 trace」—— **已实测**
- [x] 观测降级策略：未配 key / SDK 初始化失败 / flush 失败一律 no-op，不影响业务
- [x] `GET /observability/status` + `POST /observability/smoke` 两个自检接口
- [x] 修掉四个被「真实配置」暴露的问题：测试依赖本机 `.env`、`CORS_ORIGINS` 解析、
      FastAPI 原生 OTel 噪声、SDK `get_trace_url` 的 308（见 §7.5~§7.8）
- [ ] `tracing.py` 里的 `LoggingConfigurator` 占位仍待换成把日志也送进 Langfuse 的实现
      （M0-8 只做了 trace，没做 log 汇聚；原计划在本任务内，实际留到 M8）

### M1 起点（垂直切片）
- [ ] M1-1 数据模型 v0（用户 / 经历条目 / JD / 简历），把 `db.py` 换成正式仓储层
- [ ] 届时统一迁移执行器，去掉 compose 里逐个文件挂载 migration 的临时做法
- [ ] M1-6 三种 PDF 导出方案对比（**本项目最大技术风险**）

### 已被 M0 影响、后续要注意的点
- `.npmrc` 里现在只有说明性注释，没有绕行参数。开启开发者模式后无需再改
- `.python-version` 是 3.13，若要严格对齐文档的 3.12，需要在能访问 GitHub 的网络下拉 CPython 3.12
- CI 的 `types-sync` job 依赖 `schema.d.ts` 已提交；如果它没被提交，CI 会在 `git diff --exit-code` 这一步失败 —— 这是设计意图

---

## 10. 提交历史

仓库已 `git init`（默认分支 `main`，身份 `廖昊 <3344264178@qq.com>`），按逻辑边界切成 8 个提交，便于逐条回溯：

| # | commit | 内容 |
|---|---|---|
| 1 | `chore: 初始化 monorepo 工作区与工程约定` | 工作区声明、根脚本、`.gitignore`/`.editorconfig`/`.npmrc`/`.python-version` |
| 2 | `docs: 补齐需求、任务分解、技术选型与 M0 交付总结` | `requirements.md`、`tasks.md`、`docs/tech-stack.md`、`docs/M0-summary.md` |
| 3 | `feat(api): 搭起 FastAPI 后端骨架与健康检查（M0-3）` | `apps/api/**`（含 §7.4 的类型契约修复） |
| 4 | `feat(web): Next.js 15 前端外壳与 M0 自检台（M0-2 / M0-7）` | `apps/web/**` |
| 5 | `feat(types): 打通 OpenAPI → TypeScript 类型管线（M0-6）` | `packages/api-types/**` |
| 6 | `feat(db): Postgres+pgvector 编排与 Supabase migration（M0-4 / M0-5）` | `docker-compose.yml`、`supabase/**` |
| 7 | `chore(scripts): 工程脚本、issue 定义与验收脚本` | `scripts/**` |
| 8 | `ci: 加入 GitHub Actions 四作业流水线` | `.github/workflows/ci.yml` |

**未入库的内容**（`.gitignore` 已覆盖）：`node_modules/`、`.venv/`、`.next/`、`apps/api/openapi/openapi.json`、`.workbuddy/`、`scripts/_*`（本机调试时的命令输出留档）。
`schema.d.ts` 虽为生成产物但**故意入库**，理由见 §4.1。

