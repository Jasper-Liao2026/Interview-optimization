# M4 交付总结 · 并行改写与任务恢复

2026-10-10，M4-1 至 M4-7 已完成。M3 代码审核同步修复中文否定匹配、重复要求计分、空白视觉转录、float32 向量校验及前端生成入口问题，详见 [M3 总结](M3-summary.md)。

## 交付行为

生成链路使用 LangGraph `StateGraph`：`fan_out` 为每条经历发送独立任务，`rewrite_item` 仅接收岗位画像和当前经历，`fan_in` 按原始选择顺序组装成功条目。默认最多 4 条并行，可通过 `REWRITE_MAX_CONCURRENCY` 配置。公司、角色和时间直接从素材快照复制。

单条模型错误或事实违规成为独立失败结果，成功条目仍可预览和导出。失败条目通过 `POST /api/v1/resumes/runs/{run_id}/retry/{index}` 重试，保留其他条目的结果和简历 ID。

客户端可指定 `run_id`。重复提交相同请求恢复或读取该任务；同一 ID 携带不同请求返回 409。Postgres advisory lock 串行处理同一任务，确定的 JD ID 和简历 ID 避免重复落库。

`generation_runs` 保存请求、岗位画像和素材快照；官方 `AsyncPostgresSaver` 保存图状态及并行任务的完成写入。进程中断后通过 `POST /api/v1/resumes/runs/{run_id}/resume` 恢复，已完成的兄弟任务不重复改写。单条尚未完成的模型调用可能再次执行。

前端 `/generate` 记录上次任务 ID，刷新后读取结果；读取到尚未完成任务时提供恢复入口，并展示逐条失败和重试操作。接口 TypeScript 类型由 OpenAPI 生成。

## 事实约束与边界

- 每条要点必须引用可在原始描述、技能标签、定性要点或量化结果中找到的连续原文；派生 `variants` 不参与校验。
- 数字必须出现在结构化 `metrics` 且当前要点引用对应数字证据；不允许从岗位要求或派生文案引入数字。
- 已识别的技术名、英文专名须有正面事实证据，同时检查证据所在原文的否定上下文。
- 事实违规会把错误反馈给模型，最多执行 3 次事实校验尝试；仍不通过则隔离为失败条目。

审核补充：数量校验同时规范化常见中文数字（例如 `三百%` 与 metrics 中的 `300%` 等价），
但会忽略“ 一条 / 一套 ”这类通常表示交付物而非量化结果的泛化短语；RabbitMQ 等常见小写技术名也纳入技术名追溯名单。

这是确定性启发式校验，不能证明任意中文因果关系、职责级别或成果描述的语义真实性。真实模型输出通过规则仍需人工复核。技术名识别与否定规则有覆盖边界；100 次评测也不代表其他素材和模型的保证。

恢复使用生成时保存的素材与画像快照；修改素材后要创建新任务才能采用新事实。模型调用使用恢复时的当前服务配置，因此更换 provider/model 后的未完成条目可能由新模型完成。`persist=false` 只控制简历和新 JD 是否保存，任务与 checkpoint 仍保留用于恢复。

## 验收证据

| 检查 | 结果 |
|---|---|
| 后端全量 `pytest` | 267 passed（最终联合回归） |
| `node scripts/verify-m3.mjs` | 19 passed，真实 Postgres 与 stub 协议 |
| `node scripts/verify-m4.mjs` | 14 passed，真实 Postgres 与 stub 协议 |
| 并发、失败隔离和单条重试 API 测试 | 通过；成功条目调用次数保持 1 |
| Postgres saver 关闭并重新打开后的中断恢复 | 通过；完整输出恢复，成功兄弟任务不重复执行 |
| Ruff、格式检查、OpenAPI 类型同步、TS 类型检查、ESLint、生产构建 | 通过 |
| Windows 无热重载启动（`python -m app.server`） | generate/read/resume 均 200，Postgres checkpoint 写入成功 |
| Windows Selector 下 PDF 导出 | 200，合法 PDF 46384 字节、1 页；线程启动浏览器，超时回归通过 |
| 浏览器实际生成与刷新读取 | 2/2 成功；刷新后读取同一任务，新增生成 POST 为 0 |
| 浏览器 iframe 预览可见性 | 滚入视口后源事实文字可见，viewport 截图核对通过 |
| 浏览器模拟部分失败与重试 | 显示 1/2 和失败详情；仅调用失败条目 retry/1 一次，恢复 2/2 |
| 桌面 1440px / 手机 390px | 无横向溢出，无页面 JS 或 console 错误；修复手机网格被内容撑宽 |
| `deepseek-chat` 真实改写 | 100 次最终结构成功，0 事实规则拒绝，0 传输失败 |

真实评测来自 10 类固定素材，每类重复 10 次，覆盖后端、前端、观测、指标、校园、否定、注入、派生版本、数量及最小素材。报告见 [M4-eval.json](M4-eval.json)。结构失败率统计经过协议重试后的最终输出，事实拒绝统计经过事实修正后的最终结果。该样本的最终解析失败率为 0%，满足本轮 `<1%` 验收门槛；没有做语言质量盲评。

M3 的真实截图一致性和真实 embedding 语义质量仍待配置对应模型验收。当前 embedding stub 仅用于协议、缓存和数据库检索验证。

## 复现与升级

```bash
pnpm api:setup
pnpm verify:m3
pnpm verify:m4
pnpm test
pnpm lint
pnpm gen:types:check
pnpm typecheck
pnpm build
# 使用 apps/api/.env 中的真实文本模型配置，会产生模型调用费用
pnpm eval:m4 --samples 100 --concurrency 4
```

已有数据库需按 README 执行 `20261012000000_m4_generation.sql`，再重启 API。官方 checkpoint 表由 saver 首次运行时初始化。保留数据库卷即可跨进程恢复；验收脚本仅清理自己创建的 UUID。

Windows 使用 `app.server` 启动入口以提供 psycopg 所需的 Selector 事件循环。`pnpm api:dev` 已采用该入口；不带热重载运行可在 `apps/api` 执行 `.venv/Scripts/python.exe -m app.server`。
Windows PDF 浏览器子进程放在线程中执行，兼容 Selector 并保留打印超时处理。
