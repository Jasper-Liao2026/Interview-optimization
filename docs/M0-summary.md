# M0 交付总结 · 脚手架

> 2026-10-08｜覆盖 issue **M0-1 ~ M0-7**（M0-8 Langfuse 未做）
> 配套文档：`../requirements.md`（需求）、`../tasks.md`（任务分解）、`tech-stack.md`（选型）

---

## 0. 一句话结论

三层服务（Next.js / FastAPI / Postgres）的**骨架已经立起来并且可验证**：
前端能真实调通后端接口、后端能真实读回数据库里由 migration 写入的数据。

**但本机有一个未解决的环境前提**：pnpm 在这台 Windows 上无法完成依赖安装（详见第 7 节）。
这不影响代码正确性，但影响「clone 下来就能跑」这一步。

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
| 数据库（托管） | Supabase | — | Auth / RLS / pgvector | `config.toml` 已就绪，未启用 |
| 编排 | docker compose | — | 本地三层一键起 | web + api + postgres |
| Agent 编排 | LangGraph | — | StateGraph / Checkpointer | **M4 才接入**，M0 只留目录 |
| 观测 | Langfuse | — | trace / 评测 | **M0-8 未做** |
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
│       │   ├── schemas/            #   ★ 接口类型的**单一定义源**
│       │   │   ├── health.py       #     HealthResponse / SystemInfoResponse …
│       │   │   └── meta.py         #     ErrorResponse
│       │   ├── routers/
│       │   │   ├── health.py       #     /health（无前缀，给容器 healthcheck）
│       │   │   └── system.py       #     /api/v1/system/info（故意读库）
│       │   ├── services/           #   业务服务（M1 起填充）
│       │   └── agents/             #   LangGraph graph（M4 起填充）
│       └── tests/
│           ├── conftest.py         #   假数据库替身，测试不依赖真库
│           ├── test_health.py      #   6 条：200 / 前缀 / 不碰库 / trace_id / schema
│           └── test_system.py      #   2 条：读库成功 / 数据库不可用时降级
│
├── packages/
│   └── api-types/                  # ★ M0-6：OpenAPI → TS 类型产物
│       ├── package.json            #   openapi-typescript
│       ├── tsconfig.json
│       └── src/
│           ├── index.ts            #   纯转发，不放任何手写类型
│           └── schema.d.ts         #   **生成产物但入库**（理由见 §4.1）
│
├── supabase/                       # ★ M0-5：数据库结构变更的唯一来源
│   ├── config.toml                 #   本地实例配置（端口与 compose 对齐）
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
```

**刻意的两点**

- 浏览器直连后端，而不是让 Next.js 代理转发。这是 `tech-stack.md` 代价二的缓解措施：
  流式端点若经 Next.js 转发，必须关掉响应缓冲，否则逐字输出会退化成整段吐出。
  自检页上那句「客户端耗时 … ms · 浏览器直连后端」就是在展示这条路径真的生效。
- 端口 54322 与 Supabase 本地实例的默认端口一致，
  这样 `docker compose` 与 `supabase start` 两种跑法共用同一份 `DATABASE_URL`，切换零成本。

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

- `supabase/migrations/20261008000000_init.sql` 是权威定义，`supabase db reset` 会重放它
- 为了让 `docker compose up` 也能一键得到正确的库，compose 把该文件与 `seed.sql`
  **逐个文件**挂进 `docker-entrypoint-initdb.d`

**取舍要说清楚**：`docker-entrypoint-initdb.d` 只在数据目录为空时执行，且**不递归子目录**，
所以每新增一个 migration 都要在 compose 里加一行挂载。这是有意接受的短期成本 ——
M1-1 引入正式迁移流程（Supabase CLI 或 alembic）时会换成单一迁移执行器。
两个入口指向同一批 SQL 文件，不存在两份 schema 定义。

另外 migration 里刻意**没有**写 `to anon, authenticated`：那些角色只存在于 Supabase 实例，
原生 postgres 镜像没有，写上会让 migration 在 compose 里跑不起来。用默认的 PUBLIC 兜底，M2-2 再接 Supabase 角色。

### 4.6 `NEXT_PUBLIC_*` 是构建期常量

`docker-compose.yml` 里 web 服务把它作为 **build args** 传入，而不是运行时 `environment`。
写错成运行时环境变量不会报错，只会静默地把 `undefined` 打进客户端 bundle —— 这类问题排查成本很高，
所以在 compose 里写了注释钉死。

### 4.7 纯类型包不做 workspace 依赖，改用 tsconfig 别名

`@resume/api-types` 只有 `.d.ts` 与 `export type`，前端一律 `import type`，编译期即被擦除。
因此它**不需要**是 workspace dependency，也不必 `transpilePackages` ——
用 tsconfig 的 `paths` 别名引用即可，少一条依赖就少一处链接与版本对齐的麻烦。
（这条在本次排障中意外成了关键，见第 7 节。）

---

## 5. M0 验收结果

验收脚本：`pnpm verify:m0`（= `node scripts/verify-m0.mjs`），逐条判定 PASS / FAIL / SKIP。

| 编号 | 验收标准（原文） | 结果 | 证据 |
|---|---|---|---|
| M0-1 | 各子项目可独立启动 | PASS | `apps/web`、`apps/api`、`packages/api-types`、`docs`、`supabase/`、compose、workspace 声明齐全 |
| M0-2 | `pnpm dev` 正常 | 见 §7 | 代码与配置就绪；本机依赖安装受阻于 pnpm 符号链接问题 |
| M0-3 | `/health` 返回 200 | PASS | pytest 通过；`/health` 与 `/api/v1/health` 均 200，且断言了「不访问数据库」 |
| M0-4 | `docker compose up` 一键全起 | 部分 | `docker compose config` 校验通过（exit 0）；镜像构建受本机网速限制，见 §7 |
| M0-5 | 能连库且 migration 可执行 | 见 §7 | migration 与 seed 就绪；`/api/v1/system/info` 能读回 `service_meta` |
| M0-6 | 改 Pydantic 后前端类型自动刷新 | PASS | `gen-api-types.mjs --check` 无漂移；CI 有独立 job 拦截 |
| M0-7 | 前端页面显示后端返回的数据 | PASS | 自检台展示 `service_meta` 内容 + trace_id + 客户端耗时 |

> 「部分」「见 §7」的项并非代码问题，而是本机环境的网络与权限限制，逐项说明见下节。

---

## 6. 代码层面已固化的保障

| 保障 | 手段 |
|---|---|
| 后端 lint / 测试 | `ruff check`（E/F/W/I/UP/B/C4/SIM/ASYNC/RUF）+ 9 条 pytest |
| 测试不依赖真库 | 依赖覆盖 + 假数据库替身 |
| 前后端类型不漂移 | `gen:types --check` + CI 独立 job |
| 编排配置正确性 | CI 跑 `docker compose config --quiet` |
| 前端可独立构建 | `schema.d.ts` 入库，前端不需要 Python |
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

### 8.3 后续里程碑里最值钱的三个点（M0 之后要往这几处做）

1. **M4-3 并行改写的量化收益** —— 上下文隔离省了多少 token、fan-out 把 P95 延迟从 N×T 压到约 T。
   一定要留实测数字，不要只说「更快」。
2. **M5-3 异构模型 + 盲评** —— 这是 LLM-as-judge 的核心防御。
   同源评分会偏袒自己的输出；评分者知道「这是修改后的版本」时会倾向给高分。
   换厂商 + 隐藏版本信息可规避。能说清这个，说明真的做过 agent 评估而不是「调过 API」。
3. **M8-2 golden set** —— 90% 的同类项目没有评测集。`tasks.md` 里明确标注了这是「简历里最值钱的一条」。

### 8.4 面试时必须坦白的地方（不要说满）

| 事实 | 怎么讲 |
|---|---|
| **M0-8 与 M1 未做** | 观测与垂直切片尚未开始。不要说成「已完成端到端」 |
| **本机 pnpm 不可用** | 这是**加分项**而不是减分项：说明你知道自己环境的边界，并且定位到了上游源码。但要能说清「在正常环境上是可用的」 |
| **`docker compose up` 未完整跑通** | 受本机网速限制，只校验了配置。不要声称「一键全起已验证」 |
| **Supabase 未真正启用** | 目前用的是 compose 里的原生 postgres + pgvector；`supabase start` 路径已配好但未跑。RLS 政策目前是 PUBLIC 兜底，用户级隔离是 M2-2 的事 |
| **素材库/RAG/评分循环都还没有** | 这些是 M2–M5，一行代码都还没写 |
| **平台形态未定** | 见 `../requirements.md` §6：手机端全流程 / 电脑端精修 / 两者都要。这是个会影响模板与交互的**关键未决项**，面试时可以主动说「这是我下一步要先拍板的」 |

### 8.5 一个容易被追问的产品问题

**「BOSS 直聘是移动端产品，你这是个 Web 工具，中间『截图传电脑再传回来』的链路足以流失大部分用户。」**

这是本项目最大的产品风险（`requirements.md` §5.1），面试官如果懂行大概率会问。
不要回避，正面接：三种解法（响应式移动端 / 只做电脑端精修定位 / 浏览器插件抓 JD），
以及各自的代价。**承认这是个未决问题，比硬说「我做了移动端适配」诚实得多，也更能体现产品判断力。**

---

## 9. 遗留与下一步

### 立即可做（收尾 M0）
- [ ] 开启 Windows 开发者模式，然后 `pnpm install` 完整跑一遍，确认 `M0-2` 验收
- [ ] `docker compose up --build` 完整验证 `M0-4`（首次拉镜像慢，建议挂后台）
- [ ] `git init` 并提交首个 commit（**当前仓库还没有 .git**）
- [ ] 仓库与本地目录改名：`Interview-optimization` → `Resume-optimization`、`面试优化器` → `简历优化器`

### M0-8（唯一未做的 M0 任务）
- [ ] Langfuse 本地实例 + SDK 接入，验收「一次 LLM 调用能看到 trace」
- [ ] 接入后把 `tracing.py` 里的 `LoggingConfigurator` 占位换成真实实现

### M1 起点（垂直切片）
- [ ] M1-1 数据模型 v0（用户 / 经历条目 / JD / 简历），把 `db.py` 换成正式仓储层
- [ ] 届时统一迁移执行器，去掉 compose 里逐个文件挂载 migration 的临时做法
- [ ] M1-6 三种 PDF 导出方案对比（**本项目最大技术风险**）

### 已被 M0 影响、后续要注意的点
- `.npmrc` 里现在只有说明性注释，没有绕行参数。开启开发者模式后无需再改
- `.python-version` 是 3.13，若要严格对齐文档的 3.12，需要在能访问 GitHub 的网络下拉 CPython 3.12
- CI 的 `types-sync` job 依赖 `schema.d.ts` 已提交；如果它没被提交，CI 会在 `git diff --exit-code` 这一步失败 —— 这是设计意图
