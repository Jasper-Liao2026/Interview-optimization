# M1 交付总结 · 端到端垂直切片

> 2026-10-09｜覆盖 issue **M1-1 ~ M1-7（全部完成）**
> 配套文档：`../requirements.md`（需求）、`../tasks.md`（任务分解）、`tech-stack.md`（选型）、
> `M1-pdf-export-comparison.md`（PDF 三方案对比）、`M0-summary.md`（脚手架）

---

## 0. 一句话结论

**「一条经历 + 一个 JD → 生成 → 预览 → 导出 PDF」这条链路已经真实跑通，并且在两种模式下都验证过：**
stub（无 key，确定性桩数据）与真实模型（DeepSeek，`provider=openai-compatible`）。

验收结果：`node scripts/verify-m1.mjs` → **13 通过 / 0 失败 / 0 跳过**；
后端测试 **24 → 82 条全过**；ruff 干净；前端 `tsc --noEmit` 与 `next build` 通过；
类型管线无漂移。

M1 真正的技术风险不在「能不能调到模型」，而在**中文简历的 PDF 排版**。
因此 M1-6/M1-7 花了最多力气在「导出与预览是否一致」「中文字体与分页是否正常」上，
并用可量化的探针（PyMuPDF 逐页抽取 + 文字边界测量）留下证据，而不是靠肉眼看截图。

---

## 1. 这条链路长什么样

```
POST /api/v1/resumes/generate
      │
      ├─ 1. 取素材        experiences.list_for_user        （M1-2）
      ├─ 2. 解析 JD       JdParser.parse → JobProfile      （M1-3）  一次 LLM 调用
      ├─ 3. 逐条改写      ExperienceRewriter.rewrite × N    （M1-4）  串行，每次一次 LLM 调用
      ├─ 4. 组装          assemble_sections → ResumeSection （M1-4）
      └─ 5. 落库          job_descriptions + resumes        （M1-1）

GET /api/v1/resumes/{id}/html  ← 模板渲染（M1-5）
GET /api/v1/resumes/{id}/pdf   ← 无头 Chromium 打印**同一份 HTML**（M1-6 / M1-7）
```

**两个贯穿全程的设计决定：**

1. **事实字段不进模型。** `org` / `role` / 时间区间只从原始经历条目取，
   改写模型的输出契约里压根没有这些字段（`RewrittenExperience` 只有 `bullets` 和 `summary`）。
   M4-7「不可编造」这条红线，用「模型没有机会改」来实现，成本为零、效果确定 —— 而不是事后拿校验去追。
2. **预览与 PDF 是同一份 HTML。** `/html` 给前端 iframe 预览用，`/pdf` 让无头浏览器打印同一份内容。
   所以「所见即所得」是**结构上的必然**，不是一个要靠人工反复比对维持的目标。

---

## 2. 七个子任务怎么落的

| 任务 | 落地方式 | 关键点 |
|---|---|---|
| **M1-1 数据模型** | `supabase/migrations/20261009000000_m1_core.sql`：`profiles` / `experiences` / `job_descriptions` / `resumes` 四表 + `updated_at` 触发器 + RLS + `service_meta` 升 `m1_0001` | 经历存**结构化原子条目**（org/role/日期/raw_description/skill_tags/highlights 分列），不是一个 markdown blob —— M3 的向量化与匹配依赖这个粒度 |
| **M1-2 素材录入** | `routers/experiences.py`（GET/POST/GET one/DELETE）+ `seed.sql` | **没有 UI 表单**（M2-4 才做）；`ExperienceCreate` 刻意不含 `user_id`，用户由服务端注入，避免越权写入 |
| **M1-3 JD 解析** | `agents/jd_parser.py` + `JobProfile` | `JobProfile` 一处定义、三处一致：LLM 输出契约 / `job_descriptions.parsed` 的 jsonb 形状 / 前端类型 |
| **M1-4 改写** | `agents/rewriter.py` + `agents/assembler.py` + `services/generation.py` | **刻意串行**：先证明「一次改写能得到可用文本」，并行是 M4-3。先并行会把「质量不行」和「并发有问题」两类故障混在一起 |
| **M1-5 HTML 渲染** | `render/resume_html.py` + `templates/resume_classic.html.j2` | Jinja2 `autoescape=True`（模板名是 `*.html.j2`，`select_autoescape` 会按扩展名误判为不需要转义，必须显式打开） |
| **M1-6 PDF 三方案** | `pdf/export.py`（方案 B）+ `web/.../print/[id]`（方案 C）+ `scripts/pdfproto/`（方案 A 实验） | 详见 `M1-pdf-export-comparison.md`。**主路径 B，备用 C，否决 A** |
| **M1-7 中文分页** | 模板里的 `@page` + `break-inside: avoid-page`；验收用 PyMuPDF 探针 | 构造必然跨页的简历（4 段经历 / 19 条要点 → 2 页），断言条目不跨页、中文可抽取 |

---

## 3. 验收与实测数据

### 3.1 验收脚本

```bash
node scripts/verify-m1.mjs
# === 汇总：13 通过 / 0 失败 / 0 跳过 ===
```

覆盖：migration 定义与挂载、运行态 `schema_version=m1_0001`、素材库、JD 解析、
生成、HTML 渲染（断言 `@page` / `break-inside` / 抬头姓名都在）、PDF 导出与 `X-Resume-Pages`、
中文分页探针、pytest、ruff、类型管线同步。

**这次验收走的是真实模型**（`.env` 里已配 DeepSeek）：

```
[M1-3] JD 解析 — 必备技能 3 项 / 加分项 4 项，provider=openai-compatible model=deepseek-flash stub=false
[M1-4] 简历生成 — 3 个分区 / 10 条要点，trace_id=c9a44408860d4c6680541838c6cf6b2a
[M1-6] PDF 导出 — application/pdf，约 164165 字节，X-Resume-Pages=1
```

### 3.2 M1-7 中文分页（PyMuPDF 探针）

输入是**刻意构造的必然跨页**的简历（4 段经历 / 19 条要点），断言结果：

| 断言 | 结果 |
|---|---|
| 页数 ≥ 2（确实跨页了，否则测试没意义） | **2 页** |
| 每条要点完整落在同一页（没被分页切断） | 19/19 ✓ |
| 每段经历的 org / role / 时间区间同页 | 4/4 ✓ |
| 中文可被抽取（= 字体已嵌入且带 ToUnicode 映射，不是画成图形） | 每页 ✓ |

嵌入字体：`MicrosoftYaHei` / `MicrosoftYaHei-Bold`（3 个子集）。

### 3.3 三方案样例的量化对照

同一份输入、三套导出实现（完整分析见 `M1-pdf-export-comparison.md`）：

| 指标 | A · React-PDF | B · 服务端 Chromium | C · 浏览器打印 |
|---|---|---|---|
| 页数 | 2 | 2 | 2 |
| 第 1 页字符数 | **514**（只排下 1 段经历） | 1426（3 段） | 1525 |
| 内容右边界 vs 上限 | +2.2pt | **+0.1pt** | +21.7pt（浏览器页脚 URL，非正文） |
| 依赖成本 | 48 个包 / 2154 文件 / 29.6 MB | 0（复用本机浏览器） | 0 |

C 每页比 B 多 99 字，逐项对下来正好是页眉（日期 + 标题）与页脚（URL + 页码）——
**扣掉后正文差 3 字**，即 C 与 B 的排版等价。

---

## 4. 踩坑记录

### 4.1 react-pdf 不内置中文断行 → 中文长句**跑出纸面**

方案 A 第一版移植（字号行高边距全按 CSS 照抄）产出后量文字边界：

```
sample_A_react_pdf.pdf: 页宽 595.3pt  右边距上限 549.9pt  实测最右 599.0pt  → 越界 2 处，文字已在纸外
```

根因：react-pdf 的换行器按**空格**切词，中文没有空格 → 整段中文被当成一个不可断的「词」。
修法是注册 `Font.registerHyphenationCallback` 把含中文的词逐字拆开，修后溢出从 +49.0pt 降到 +2.2pt。

**这个坑在浏览器侧根本不存在**（Blink 自带 CJK 断行）。这正是「第二套排版代码」的真实成本：
不只是多写一遍样式，而是**多踩一遍渲染引擎的坑**。

### 4.2 iframe 里的 `@page` 不作用于顶层打印任务 → 方案 C 打出**空白首页**

最初的方案 C 是把 HTML 塞进 `srcDoc` iframe 再 `window.print()`，实测 3 页、**第 1 页 0 字符**。
两层原因叠加：① `@page` 只对「被打印的那个文档」生效，而这里被打印的是父文档；
② 模板在 `@media print` 下又把 `.page` 的内边距清成 0（因为它假设边距由 `@page` 提供）。

修法不是继续加 padding（那是在错误的一层打补丁），而是换承载体：
用 `document.write` 把简历 HTML 提升为**顶层文档**，让它自带的 `@page` 与 `@media print` 作用在正确的对象上。
修后 3 页 → 2 页，第 1 页 0 → 1525 字符，且与 B 的正文排版一致。

> 可复用的经验：**同一份 HTML 用于「打印」时让它当顶层文档，用于「预览」时才放进 iframe。**

### 4.3 类型管线的老坑又犯一次（并且这次是构建拦住的）

`GenerateRequest.persist` 带 `default=True` 字面量 → 生成的 TS 类型里它是**必填**，
而前端 `generate/page.tsx` 只传了 `jd_text` 与 `experience_ids` → `tsc` 报错、`next build` 失败。

修法是**在前端显式传 `persist: true`**，而不是加一个 `?? true` 之类的兜底去掩盖契约定义
（沿用 M0 那次 `meta` 契约 bug 的处理原则：回到单一定义源，不在消费端打补丁）。

### 4.4 `llm_max_tokens=256` 会把结构化输出截断

M0 定 256 只是为了验证观测链路。M1 要输出完整 `JobProfile` 与多条改写要点，
256 token 会截成不完整 JSON，表现为「解析失败 → 重试 → 仍失败」。
改为 2048，并给 OpenAI 兼容端点加上 `response_format={"type":"json_object"}`。

**两道防线各管一件事**：`response_format` 由服务端保证「是合法 JSON」，
schema 校验 + 重试负责「字段齐全、类型正确」。不要用其中一个替代另一个。

### 4.5 探针自己写错了断言（假失败）

第一版分页探针按 `org + role` 拼成连续串去页面里找，结果 4 段经历全部「抬头丢失」。
真相是模板里两者之间有分隔符（`·`），去空白归一化也去不掉它。
改为**逐要素判定**（org / role / period 三者是否出现在同一页）后 `ok=True`。

**教训**：探针失败时先怀疑探针。断言写得比被测系统更严，只会产出噪声。

### 4.6 样例写库撞外键

三方案共用一份样例简历，但它是在内存里造的，`user_id` 是 `uuid4()`，
写进 `resumes` 时直接撞 `resumes_user_id_fkey`。
改用配置里的 `dev_user_id`（与 `seed.sql` 里那个固定 UUID 一致）—— 也印证了 `seed.sql` 与 `settings.dev_user_id` 必须同步这件事。

### 4.7 PyMuPDF 不进产品依赖

分页与字体校验需要一个 PDF 解析库，但它只服务于「验证」，不该出现在 `apps/api` 的依赖里。
做法：`uv run --no-project --with pymupdf python scripts/m1_pdf_probe.py` —— 临时环境，用完即弃。
验收脚本里也把 uv 当作**可选外部工具**，找不到就 SKIP 而不是判 FAIL。

### 4.8 一处已知的「不可达防御代码」

`assemble_sections` 里有一段「未知 kind 单独成区，不静默丢弃」的兜底分支，
但 `ResumeEntry.kind` 是 `Literal["project","internship","campus"]`，
migration 侧也有同名 check 约束 —— 两层都挡住之后，这段分支实际**不可达**。
测试里已把它固定成「非法 kind 会被拒绝」，并在注释里标注现状；
若将来把 Literal 放宽成 `str`，它会立刻变成一条真路径。

---

## 5. 交付物清单

**后端（`apps/api`）**
```
app/schemas/experience.py       app/schemas/jd.py            app/schemas/resume.py
app/repositories/               （experience / jd / resume / profile 四个仓储）
app/llm/structured.py           （extract_json / json_instructions / fixture_payload）
app/llm/client.py               （+ complete_json：JSON 模式 + schema 校验重试）
app/agents/prompts.py           app/agents/jd_parser.py
app/agents/rewriter.py          app/agents/assembler.py
app/render/resume_html.py       app/render/templates/resume_classic.html.j2
app/pdf/export.py               app/services/generation.py
app/routers/experiences.py      app/routers/jd.py            app/routers/resume.py
tests/test_structured_output.py test_assembler.py  test_render_resume.py
tests/test_pdf_export.py        test_api_m1.py
```

**前端（`apps/web`）**
```
src/app/generate/page.tsx       （JD 输入 + 经历勾选 + 结果展示 + iframe 预览）
src/app/print/[id]/page.tsx     （方案 C：把 HTML 提升为顶层文档后 window.print）
src/components/top-nav.tsx      src/lib/api-client.ts（+ M1 类型与四类端点）
```

**数据与脚本**
```
supabase/migrations/20261009000000_m1_core.sql   supabase/seed.sql
scripts/verify-m1.mjs                            （M1 一键验收）
scripts/m1_paginate.py  scripts/m1_pdf_probe.py  （M1-7 中文分页验证）
scripts/m1_make_samples.py  scripts/m1_text_bounds.py  （M1-6 三方案样例与边界测量）
scripts/pdfproto/       （方案 A 的实验代码；自带 package.json，不进产品依赖）
```

> 这些脚本**入了库**（不再算「临时产物」）：`verify-m1.mjs` 与两份 M1 文档都直接引用它们，
> 若不入库，新克隆的仓库里那些命令会指向不存在的文件。
> 它们跑出来的 PDF / PNG / JSON 仍然落在 `scripts/_m1_out/`（该目录被忽略，按 §7 复现）。

**文档**
```
docs/M1-summary.md                （本文）
docs/M1-pdf-export-comparison.md  （三方案对比 + 四维矩阵 + 复现命令）
```

**接口面**：OpenAPI **12 个 path**（M0 是 5 个）。

---

## 6. 未完成 / 留给后续

| 项 | 说明 | 归属 |
|---|---|---|
| 真并行改写 | M1 刻意串行；fan-out/fan-in 与并发限流 | M4-3 / M4-4 |
| LangGraph 接管编排 | `services/generation.py` 现在是一条显式顺序流程，节点边界已清楚，搬进 StateGraph 是机械改写 | M4-2 |
| 多模板 | `TEMPLATE_FILES` 现在只有 `classic`，注册表已预留 | M7-1 |
| 批量直出 | 目前一次一个 JD；批量要并发 + 队列，且**只能走方案 B**（C 需要人工点打印） | M7-5 |
| 前端素材录入 UI | 现在只能靠 `POST /experiences` 或 seed 脚本 | M2-4 |
| 用户级隔离 | RLS 已开但只有读策略；`get_current_user_id` 仍硬编码开发用户 | M2-2 / M2-7 |
| 推送远端 | 本地已提交但**未推送**（代理阻塞 `gh auth refresh`，且 token 缺 `workflow` scope） | 见 M0-summary §7 |

---

## 7. 面试可讲点

**① 把「不可编造」做成结构约束，而不是事后校验。**
改写模型的输出契约里只有 `bullets` 文本，`org` / `role` / 时间区间根本不在它的输出里 ——
模型连编造公司名的机会都没有。成本为零，效果确定。这比「先让模型写、再拿规则去查它有没有瞎写」可靠得多。

**② 判据选对了，方案就不用争。**
PDF 三方案都能出文件；把判据从「能不能出」换成「导出与预览的一致性是不是结构保证」，
方案 A 自然出局，不需要比较谁的字更好看。而 B/C 的一致性可以量化证明：
C 每页比 B 多 99 字，逐项对下来正好是浏览器加的页眉页脚，扣掉后正文差 3 字。

**③ 跨渲染引擎的真实成本是「多踩一遍坑」。**
react-pdf 不内置 CJK 断行，中文长句会直接跑出纸张（实测最右 599.0pt > 页宽 595.3pt）。
这个坑在浏览器侧根本不存在 —— 写第二套布局代码的代价，不只是多写样式。

**④ 修 bug 前先判断「该补哪一层」。**
方案 C 的空白首页，加 padding 是在错误的一层打补丁；把 HTML 提升为顶层文档，
才是让 `@page` 作用到正确的对象上。3 页空白页 → 2 页正常。

**⑤ 探针失败时先怀疑探针。**
分页断言第一版按 `org+role` 连续串匹配，被模板里的 `·` 分隔符骗了，4 段经历全报「丢失」。
断言写得比被测系统更严，只会产出噪声。

**⑥ 无 key 也要能端到端验证。**
stub provider 用「按 JSON Schema 生成的确定性桩数据」打通整条链路，
且所有字符串都带 `【fixture】` 前缀、`is_stub=True` —— **绝不伪装成真实输出**。
这让 M1 最大的风险（PDF 排版）可以在没有 API key 的 CI 里被反复验证。

**⑦ 否决一个方案时要留下它的证据。**
方案 A 的实验环境（`scripts/pdfproto/`）与其「中文溢出 49pt / 第 1 页只用三成」的实测数据都留在仓库里，
而不是删掉后只留一句「A 不好」。
