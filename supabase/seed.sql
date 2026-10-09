-- ============================================================
-- 本地开发种子数据
-- ============================================================
-- 幂等：可重复执行（on conflict 时更新）。
-- `supabase db reset` 会自动应用 migrations + seed.sql；
-- docker compose 首次初始化时也会执行本文件。
--
-- 执行顺序（见 docker-compose.yml 的挂载编号）：
--   10-init.sql → 11-m1-core.sql → 20-seed.sql
-- 因此这里可以直接引用 M1 建出来的表。

-- ---------------------------------------------------- 服务自检元数据
insert into public.service_meta (key, value)
values
  ('service_name',   'resume-optimizer-api'),
  ('milestone',      'M1'),
  ('schema_version', 'm1_0001'),
  ('stack',          'Next.js 15 + FastAPI + LangGraph + Supabase')
on conflict (key) do update
  set value      = excluded.value,
      updated_at = now();

-- ------------------------------------------------------------ 开发用户
-- M1-2 的「用户先硬编码」就落在这里：固定 UUID，方便脚本与接口反复引用。
-- M2-7 接入 Supabase Auth 后，此处改为与 auth.users 对齐。
insert into public.profiles (id, display_name, headline)
values (
  '00000000-0000-4000-8000-000000000001',
  '本地开发用户',
  '后端 / AI 应用开发'
)
on conflict (id) do update
  set display_name = excluded.display_name,
      headline     = excluded.headline;

-- -------------------------------------------------------- 一条示例经历
-- 垂直切片的输入素材。raw_description 是事实基线，改写必须可回溯到这里。
insert into public.experiences (
  id, user_id, kind, org, role, start_date, end_date,
  raw_description, skill_tags, highlights, sort_order
)
values (
  '00000000-0000-4000-8000-000000000101',
  '00000000-0000-4000-8000-000000000001',
  'project',
  '简历优化器',
  '独立开发',
  date '2026-09-01',
  null,
  '独立设计与实现一个批量生成岗位适配版简历的 Web 工具。'
  || '后端用 FastAPI 承载全部业务逻辑，用 LangGraph 编排「JD 解析 → 经历改写 → 评分回环」的多步流程；'
  || '前端用 Next.js 15 只负责渲染与流式消费。为避免模块级返工，先打通「一条经历 → 一份 PDF」的垂直切片，'
  || '再横向补模块。接入 Langfuse 观测，每次 LLM 调用都能按 trace_id 回放。',
  array['Python', 'FastAPI', 'LangGraph', 'Next.js', 'PostgreSQL', 'Langfuse'],
  array[
    '把每个请求的 trace_id 复用为 Langfuse trace_id，前后端日志与 LLM 调用可用同一 ID 回放',
    '数据库连接池惰性建立，postgres 慢启动不再拖死 API 进程',
    '用 OpenAPI 自动生成前端 TS 类型，消除前后端类型双写'
  ],
  0
)
on conflict (id) do update
  set org             = excluded.org,
      role            = excluded.role,
      raw_description = excluded.raw_description,
      skill_tags      = excluded.skill_tags,
      highlights      = excluded.highlights;
