# M3 交付总结 · JD 解析与匹配

> 2026-10-10 · 对应 M3-1 至 M3-6
>
> M3 专项测试：125 passed；本轮真实 API/Postgres 验收：19 passed；真实文本模型评测：10/10；类型管线、构建与 lint 均通过。M3/M4 联合回归见 M4 总结。

## 1. 交付内容

M3 把 JD 从一段待处理文本变成可保存、可复用、可解释的匹配输入：

- `POST /api/v1/jd/parse` 解析文本 JD，输出结构化 `JobProfile`。
- `POST /api/v1/jd/parse-image` 接收 PNG/JPEG/WebP，直接把图片交给视觉模型；不做 OCR 中间层。
- `/jobs` 支持粘贴文本、上传截图、保存 JD、编辑元数据、删除 JD 和查看匹配矩阵。
- 保存后的 JD 可以直接进入 `/generate?jd=<id>`，生成请求使用 `jd_id`，不会再次解析或复制 JD。
- 经历事实字段写入 1536 维 pgvector，并使用 HNSW 余弦索引做候选粗筛。
- 每条经历 × 每条 JD 要求都返回完整矩阵：状态、规则分、语义相似度、证据和原因。

## 2. 数据与匹配边界

迁移文件为 [`20261011000000_m3_matching.sql`](../supabase/migrations/20261011000000_m3_matching.sql)。它新增 `job_descriptions.source_type`、`experience_embeddings`、HNSW 索引和事实字段变更触发器。现有数据库可非破坏升级，详见 README 的升级步骤。

向量来源只包括组织、角色、原始描述、技能标签、定性要点和量化结果；`variants` 不参与检索和证据，避免把派生文案当成事实。事实变化会删除旧向量，下一次匹配按事实哈希和模型端点重新生成；删除经历由外键级联清理向量。

规则命中优先于向量：完整事实命中为 `covered`（85 分），仅语义相关最高为 `related`（49 分），未命中且相似度不足为 `missing`。别名覆盖 PostgreSQL/pg、Kubernetes/k8s、JavaScript/JS、Node.js/Nodejs 等，并使用边界匹配避免 Java/JavaScript、SQL/NoSQL 等误命中。否定分句（如“没有 Kubernetes 经验”）不会被当作覆盖证据。

分数是解释匹配矩阵的规则分，不是录用概率；候选粗筛之外，完整事实命中仍会被纳入结果。JD 的硬性要求、加分项、职责和业务域分别保留，未知信息为 `null`，JD 内的指令只作为数据处理。

## 3. 配置

无 key 时默认使用明确标注的 stub：文本 JD 可以验证协议和界面，向量是 SHA256 哈希演示，不代表语义质量。真实服务分别配置：

```env
LLM_PROVIDER=openai-compatible
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=...
LLM_MODEL=deepseek-chat

# 视觉模型可独立于文本模型；留空时复用 LLM 配置
VISION_PROVIDER=openai-compatible
VISION_BASE_URL=https://api.openai.com/v1
VISION_API_KEY=...
VISION_MODEL=gpt-4.1-mini

EMBEDDING_PROVIDER=openai-compatible
EMBEDDING_BASE_URL=https://api.openai.com/v1
EMBEDDING_API_KEY=...
EMBEDDING_MODEL=text-embedding-3-small
```

embedding 客户端会校验返回顺序、数量、1536 维、有限数值和非零向量。视觉图片限制为 PNG/JPEG/WebP、5 MiB 和 2000 万像素以内。

## 4. 验收证据

| 检查 | 结果 |
|---|---:|
| M3 专项 `pytest` | 125 passed |
| `node scripts/verify-m3.mjs`（stub API + 真实 Postgres，本轮复验） | 19 passed |
| `node scripts/verify-m1.mjs` | 13 passed |
| `node scripts/verify-m2.mjs` | 16 passed |
| `ruff check` / `ruff format --check` | 通过 |
| `pnpm lint` / `pnpm typecheck` / `pnpm build` | 通过 |
| 浏览器桌面与 390px 移动流程（stub API + 真实数据库） | 通过，无页面错误和横向溢出 |
| 真实文本模型 10 份 JD | 10/10，结果见 [`M3-eval.json`](M3-eval.json) |

文本评测有一次请求超时，重试后通过；报告保留了首次失败记录。真实视觉模型和真实 embedding 模型尚未验收：当前视觉配置回退到不支持图片的文本模型，embedding 仍为 stub。可配置对应 provider 后运行：

```bash
pnpm eval:m3 --images --embeddings
```

协议、校验、缓存失效和截图错误路径已有自动化测试；真实截图与语义排序质量属于配置模型后的验收项。

### 本轮验收修复

- 保存 JD 后详情或列表读取失败：保留已保存状态，详情恢复只重试 GET；浏览器注入两种 503 后均确认仅提交一次 POST，避免重复保存。
- 否定技能列表：英文逗号列表以及“没有使用过”“不具备”不再被当作覆盖证据，同时保留独立正面分句的事实。
- 截断图片：在图片头部校验后完整解码，拒绝损坏的 JPEG/WebP；新增回归测试后全量 211 条通过。

最终生产构建的桌面、390px 移动端、JD 复用生成和 iframe 预览流程通过，无页面 JS 错误和横向溢出。代码与验收证据随本次 M3 提交交付。

GitHub 状态已核实：[#27](https://github.com/Jasper-Liao2026/Interview-optimization/issues/27)、[#29](https://github.com/Jasper-Liao2026/Interview-optimization/issues/29)、[#31](https://github.com/Jasper-Liao2026/Interview-optimization/issues/31)、[#32](https://github.com/Jasper-Liao2026/Interview-optimization/issues/32) 已关闭；[#28](https://github.com/Jasper-Liao2026/Interview-optimization/issues/28)、[#30](https://github.com/Jasper-Liao2026/Interview-optimization/issues/30) 及总 issue [#26](https://github.com/Jasper-Liao2026/Interview-optimization/issues/26) 保留开放，跟踪真实模型验收。

## 5. 常用命令

```bash
pnpm verify:m3             # 真实 API + Postgres 验收并清理自建数据
pnpm eval:m3               # 真实文本模型评测（需要 LLM key）
pnpm eval:m3 --images --embeddings
```

验收脚本只删除自己创建的 UUID；不会清空已有素材或 JD。

## 6. M4 开始前的 M3 审核补充（2026-10-10）

- 修复中文否定事实误判：`无 Python 开发经历`、`不使用 Python`、`不擅长 Python` 等不再算作技能覆盖；混合正反分句仍保留正面证据。
- 同一要求若同时出现在必备技能、加分项、职责或业务域，矩阵只保留优先级最高的首次出现项，避免重复计分。
- 视觉模型若返回只有空格或换行的 `raw_text`，结构化校验失败且不会落库。
- embedding 响应在转换为 pgvector 的 float32 精度后还须有限且非零；溢出或下溢作为嵌入服务错误返回，而非数据库写入时 500。
- 前端允许重新选择同一张截图；未解析岗位禁止进入生成；没有结构化要求的岗位仍可勾选素材。
- 验收脚本改为检查本次创建的 UUID，避免并行生成或评测时全局 JD 数量变化造成误报；合并后为 19 项检查，均通过。
- M3 专项测试 125 条通过；联合回归的后端全量测试、全仓 Ruff 与格式检查通过，最终测试数量见 [`M4-summary.md`](M4-summary.md)。
