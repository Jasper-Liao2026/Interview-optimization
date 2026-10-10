# M6 · 精调编辑页

2026-10-10。M6 交付结构化编辑、分页预览、不可变版本历史和带人工确认的局部 AI 改写。

## 能力

- `/edit` 可从最近编辑的简历或 UUID 进入编辑页；左侧编辑结构化标题、抬头、分区、经历和要点，右侧由 PDF.js 绘制实际 PDF 分页预览。
- 预览请求使用未保存草稿，预览返回的 PDF Blob 同时作为下载文件，因此下载与当前预览使用相同分页结果。
- 保存采用 `expected_revision` 乐观并发控制。每次保存、恢复和 AI 确认都追加不可变 `resume_revisions`，恢复历史版本也会产生一个新版本。
- 人工改动只保留同一经历 ID 且文本完全一致的旧事实来源；改过的要点由服务端清空证据，避免前端绕过事实保护。
- AI 微调只提交选中的经历，改写节点继续使用 M4 的事实校验。LangGraph 在提案后调用 `interrupt()`，提案、基础版本和来源快照写入 Postgres；刷新或重建服务后可以继续确认。
- 接受提案再次执行时具备幂等性；基础版本已变化时返回 409，拒绝不会改变简历版本。
- 生成任务检测 `edit_revision` 和编辑历史，不能覆盖已打开编辑器的简历。M5 评分记录保存 `resume_snapshot`，后续编辑最佳简历不会改变已完成评分历史；M6 迁移会为旧评分记录补齐快照。

## API

- `GET/PUT /api/v1/resumes/{resume_id}/editor`
- `POST /api/v1/resumes/{resume_id}/preview`
- `POST /api/v1/resumes/{resume_id}/revisions/{revision_id}/restore`
- `POST/GET /api/v1/resumes/{resume_id}/ai-edits[/{run_id}]`
- `POST /api/v1/resumes/{resume_id}/ai-edits/{run_id}/decision`

业务逻辑和版本事务在 Python 服务与 Postgres 中完成，前端只负责表单、预览和确认交互。接口类型由 Pydantic/OpenAPI 生成。

## 迁移与运行

已有 M5 数据库执行：

```bash
docker cp supabase/migrations/20261014000000_m6_editing.sql resume-postgres:/tmp/m6-editing.sql
docker exec resume-postgres psql -U postgres -d resume_optimizer -v ON_ERROR_STOP=1 -f /tmp/m6-editing.sql
```

新数据库由 compose 自动挂载 M6 migration。迁移后重启 API，并运行 `pnpm gen:types` 刷新接口类型。

## 验收

| 检查 | 结果 |
|---|---|
| `pnpm verify:m6` | 26 项真实 Postgres、LangGraph checkpoint、Chromium PDF 检查通过 |
| M6 定向测试 | 23 项通过，覆盖 CAS、持久化 interrupt、越权、事实校验和 checkpoint 丢失 |
| 后端全量 pytest | 327 项通过 |
| `pnpm verify:m5` | 19 项通过 |
| Ruff、ESLint、TypeScript、OpenAPI 同步、生产构建 | 通过 |
| Edge 浏览器操作 | 加载、未保存 PDF 下载不落库、保存、AI 刷新恢复、确认、拒绝、撤销通过；无页面错误 |
| PDF.js 预览 | 桌面与手机完整显示页面，中英文字体显示正确；4 页 PDF 的绘制页数与响应页数一致，下载字节与预览 PDF 完全一致 |

验收使用 `LLM_PROVIDER=stub`，只能证明流程、事实规则和持久化行为；真实模型改写质量仍需配置真实模型后人工评估。

人工编辑的事实由用户核对，不能把编辑后留下的文字视为 AI 事实校验结果。AI 微调需要绑定已解析 JD、保留对应原始素材；手工新增且无素材来源的经历不能进行 AI 微调。
局部改写可能形成混合来源，无法可靠证明单一生成厂商时保守将厂商标记置空；真实 M5 异构评分会拒绝无法证明来源的简历。
旧 M5 记录的迁移快照使用升级时数据库中的最佳简历内容，不能重建升级前已被外部修改的内容。

## 独立代码审核（2026-10-10）

审核通过。检查覆盖版本事务、并发保存、持久化人工确认、生成防覆盖及评分历史隔离。
审核发现长经历的要点与证据会挤掉反馈末尾的用户指令；已修复为先完整保留用户指令，再附带有界要点文本，事实证据继续由原始素材提供。
新增回归捕获实际模型 prompt，验证 40 条长要点下近 2000 字的用户指令仍完整送达。
修复后 M6 定向测试 24 项通过，真实 Postgres/checkpoint/Chromium 验收 26 项通过；真实模型质量的验收边界保持如上。
