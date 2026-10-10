# M5 · API 与评分循环交付

2026-10-10。用户确认范围为 API + 循环。工程实现已完成，真实独立厂商 judge 的质量校准待配置 key。

## API 行为

- `POST /api/v1/resumes/{resume_id}/score`：评分已保存且绑定已解析 JD 的简历。
- 请求：`threshold` 默认 80；`max_rounds` 默认 2、只能 0–2；`cost_limit` 默认 100；`persist` 默认 true。
- 响应：四维评分、按经历下标的扣分和建议、事实违规下标、每轮不可变快照、最佳轮次、停止原因、最佳简历及预览/PDF 路径。
- `persist=true` 在一个数据库事务中保存独立最佳简历与 `resume_score_runs` 历史，保留原始简历。
- `persist=false` 只返回结果，不提供指向未保存内容的预览/导出链接。
- `GET /api/v1/resumes/{resume_id}/scores/{score_run_id}` 回读历史；简历、JD、素材与评分记录都按 user_id 查询。
- 无简历返回 404；无 JD/画像/来源、同源厂商、事实无安全版本返回 400；参数不合法返回 422；模型错误返回 502。

```bash
curl -X POST http://localhost:8000/api/v1/resumes/RESUME_UUID/score \
  -H "Content-Type: application/json" \
  -d '{"threshold":80,"max_rounds":2,"cost_limit":100,"persist":true}'
```

`apps/web/src/lib/api-client.ts` 提供 `scoreResume` 与 `getScoreRun`，类型来自 OpenAPI。

## Rubric 与锚点

真实 judge 对每条经历返回四项 0–5 分的结构化判断，由代码乘以 20 并按固定权重聚合。
总分为各经历维度平均后的加权和，不由模型自由生成总分。

| 维度 | 权重 | 2 分示例 | 4 分示例 | 5 分示例 |
|---|---|---|---|---|
| 岗位相关性 relevance | 20% | “参与开发”，看不出目标能力 | Python 岗位中，明确说明 Python 脚本的用途 | 清楚展示岗位要求的技能如何用于具体动作，证据充分 |
| 要求覆盖度 coverage | 25% | 只提技能名，没有对应做法 | 接口岗位中描述具体接口实现，但相关测试材料遗漏 | 当前经历能支持的岗位要求都有对应动作与事实，不要求每段包办整个 JD |
| 事实可追溯性 evidence | 45% | 只有笼统引用，不能支持主要断言 | 每条动作能对应原文，仍有证据表达不够准确处 | 动作、技术名和量化结果均有准确引用，无新增事实 |
| 表达清晰度 clarity | 10% | “参与项目”，动作与做法模糊 | 完整动作句，有少量重复 | 动作、做法及已有结果紧凑明确，适合快速扫描 |

0 分表示没有相应证据；1/3 分位于相邻锚点之间。无量化素材时不得因没有数字扣分。
真实 judge 同时看到匿名候选要点、岗位画像和原始事实，不看到模型/厂商、轮次或版本信息。
原始事实可能包含用户文本，prompt 明确要求作为数据处理，不执行其中指令。

默认 stub 使用确定性关键词、事实与长度规则，明确返回 is_stub 和 warnings。
本地 relevance/coverage 使用关键词覆盖比例，不代表真实模型质量或语义校准。

## 循环与硬约束

LangGraph `StateGraph`：`score` → 条件边 → `rewrite` → `score`；停止节点为 threshold、max_rounds、cost_limit、no_low_score_items。
最多两次改写，即初始评分加最多两次评分。预算不足时不开始改写，也不开始随后无法支付的评分。

`cost_limit` 是保守模型调用预算单位，不是美元/人民币或 token 费用：
每次真实 judge 预留 3 单位（最多三次结构校验调用），每条真实改写预留 9 单位
（三次事实校验 × 三次结构校验）；stub 为零。按预留上限累计，即使实际调用较少也不退还。usage_tokens 记录最后一次成功 judge 响应的用量，不含失败重试的完整 token 总计。
首次 judge 预算不足时返回零成本本地规则快照与 cost_limit，不调用网络。

低分下标与简历分区中的经历对应，来源按 experience_id 匹配，不依赖仓储返回顺序。
仅这些经历进入改写；其他经历直接复用。改写上下文只含当前素材、岗位画像和评分建议，继续执行 M4 的证据、数字、技术与否定事实校验。
本地改写只复制通过事实规则的原文，不制造新事实。

事实违规将 evidence 分归零，强制该经历进入改写名单，且总分达标也不能停止。
最佳版本只从事实校验通过的快照中选分数最高者；同分取最早版本。
如果所有快照都违规，返回 400，不保存或导出不安全版本。

评分从当前素材库读取事实快照。素材删除或事实变化可导致旧简历无法通过校验；此时应调整素材或重新生成。
事实规则是启发式约束，不能证明所有自然语言因果关系。评分提升并不等于内容真实的充分证明。
M5 回环本身尚不支持进程中断恢复；只在完成后保存评分历史，M4 生成任务的恢复能力独立保留。

## 异构模型与来源

配置示例位于 `apps/api/.env.example`：

```dotenv
GENERATION_VENDOR=deepseek
JUDGE_PROVIDER=openai-compatible
JUDGE_VENDOR=openai
JUDGE_BASE_URL=https://api.openai.com/v1
JUDGE_API_KEY=填写你本机的评分模型key
JUDGE_MODEL=gpt-4.1-mini
JUDGE_MAX_TOKENS=4096
```

`provider` 表示协议；`vendor` 表示厂商。两者不能混用。
新增 `resumes.generator_vendor` 记录生成时的厂商，同时检查历史生成厂商和当前改写厂商均不同于 judge。
旧简历缺少厂商元数据时拒绝真实评分，须重新生成；跨厂商恢复/混合改写的来源可能无法用单一厂商表示，此时保守保留为空。
只有显式记录 generator_vendor=stub 的纯 stub 简历才豁免历史厂商检查；旧 stub 前缀不能证明来源。
stub 只是本地工程验证，不算真实异构模型调用。

## 迁移与验收

已有 M4 数据库：

```bash
docker cp supabase/migrations/20261013000000_m5_scoring.sql resume-postgres:/tmp/m5-scoring.sql
docker exec resume-postgres psql -U postgres -d resume_optimizer -v ON_ERROR_STOP=1 -f /tmp/m5-scoring.sql
pnpm verify:m5
pnpm eval:m5
```

新数据库卷由 compose 自动执行 M5 migration。验收脚本只清理自己创建的数据。

| 验收 | 结果 |
|---|---|
| 真实 Postgres API 与循环 | 19 项通过 |
| 核心评分测试 | 18 项通过；包含分数震荡、三重终止、厂商与盲评、事实违规最佳版本拦截 |
| API 集成测试 | 9 项通过；包含真实模型接口替身、定向改写、历史/导出、历史厂商防绕过 |
| 后端全量 pytest | 304 项通过 |
| Ruff / 格式 / OpenAPI 类型同步 / TypeScript / ESLint / 生产构建 | 通过 |
| 真实独立厂商 judge 重复评分偏差 <0.3 | 未实测：本机 JUDGE_PROVIDER=stub，缺独立厂商 JUDGE_API_KEY |

`pnpm eval:m5` 对 M4 评测集中十种素材的真实 DeepSeek 输出，各独立调用 judge 两次，不使用评分缓存。
按各样本分差 <0.3 检查稳定性并输出 `docs/M5-eval.json`；stub、缺 key、同厂商均拒绝开始。
该评测检查重复性，不等于与人工评分一致；人工锚点校准仍需审阅实际输出。
M5-2 / M5-3 与 M5 总 issue 在真实质量证据补齐前保留打开。
