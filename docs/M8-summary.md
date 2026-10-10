# M8 · 观测与评测

2026-10-11，M8-1 至 M8-5 完成。生成请求沿用前端 `X-Request-Id` 作为规范化 trace，Langfuse 观测失败会降级，不影响业务结果。

## 交付

- 每次 JD 解析、改写、结构化重试和失败调用都记录 provider、model、prompt 版本 hash、token、延迟、费用和状态；Postgres 保存 `generation_llm_calls`，生成运行保存汇总。
- `/api/v1/observability/usage` 和前端 `/observability` 展示任务级用量、总量、未知用量和 trace 链接。
- `app/evaluation/golden_dataset.json` 含 20 条明确 `synthetic=true` 的 JD、期望画像、经历与期望要点；`eval_m8.py` 固定对同一数据集运行两版 prompt。
- `m4.0` / `m8.0` 是不可变 prompt snapshot，记录 SHA-256；Postgres 保存版本、选择历史和前一版本，支持回滚。
- 真实评测拒绝 stub、同厂商 judge、缺少 key 和质量回归；stub 只用于工程冒烟，并在报告中明确标记。

## 验收

- `pnpm api:test`：376 项通过。
- `pnpm verify:m8`：10 项 telemetry + 5 项 Postgres prompt/dataset 验收通过。
- `pnpm verify:m8 -- --langfuse`：16 项真实 Postgres / Langfuse 检查 + 5 项 prompt/dataset 检查通过，验证并行父子 span、实际 prompt/输出回放及 usage。
- `pnpm eval:m8 -- --stub`：40 个样本结果（两版各 20），报告含逐样本输出、期望值、失败、prompt hash 和模型信息。
- 真实 Edge 浏览器：9 项检查通过，覆盖单份和批量指标、费用、trace 链接、监控页、任务跳转与 390px 布局。
- `pnpm typecheck`、`pnpm lint`、`pnpm build` 通过。

本机系统代理曾把 localhost 请求送到代理并返回 502；验收时设置 `NO_PROXY=localhost,127.0.0.1,::1` 后真实上报与读回成功。SDK HTTP 客户端已启用重定向跟随，解决本地 public API 的 308。代理环境运行 API 时也应设置本地 `NO_PROXY`。

## 使用与升级

已有 M6/M7 数据库需应用 `supabase/migrations/20261015000000_m8_evaluation.sql`；新库由 Compose 自动加载。迁移已应用到本次开发库，不删已有数据。

`PROMPT_VERSION=m4.0` 为兼容默认，`m8.0` 加入稳定排序规则；部署时改该环境变量并重启即可选版本或回滚。新任务记录该版本，恢复旧任务仍使用原快照。`PromptVersionRepository` 保存版本选择与前一版本；评测 CLI 明确传入 `--prompt-a` / `--prompt-b`。

费用由 `LLM_INPUT_PRICE_PER_MILLION_USD` / `LLM_OUTPUT_PRICE_PER_MILLION_USD` 计算。未配置单价或 provider 未报告用量时费用为 null；失败调用计入 unknown usage，而非伪装成免费。stub 的 token 是估算、费用为零，不代表真实账单。

本机未配置真实独立 judge，本次 51 / 51 分报告仅证明工程流水线，不证明 prompt 质量。配置 `JUDGE_PROVIDER=openai-compatible`、独立厂商和 key 后运行 `pnpm eval:m8 -- --min-score 70 --max-regression 0`；任何样本失败或回归超阈值会退出非零。

生成任务记录墙钟耗时（含并行改写和持久化）；调用明细记录各 provider 的独立耗时。模型返回 usage 后、持久化之前若进程突然退出，不能保证恢复出供应商未写入本地的 token，未知账单需以供应商账单核对。
