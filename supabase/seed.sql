-- ============================================================
-- 本地开发种子数据
-- ============================================================
-- 幂等：可重复执行（on conflict 时更新）。
-- `pnpm db:reset` 会重建卷并按挂载编号重放 migrations + seed.sql；
-- docker compose 首次初始化时也会执行本文件。
--
-- 执行顺序（见 docker-compose.yml 的挂载编号）：
--   10-init.sql → 11-m1-core.sql → 12-m2-library.sql → 13-m3-matching.sql → 20-seed.sql
-- 因此这里可以直接引用 M1 / M2 建出来的表与列。
--
-- ⚠️ seed 在 migration **之后**执行，所以这里的 schema_version 必须与
--    最后一个 migration 保持一致 —— 否则会把标记「降级」回旧里程碑，
--    让 /system/info 与 verify 脚本读到一个骗人的版本号。

-- ---------------------------------------------------- 服务自检元数据
insert into public.service_meta (key, value)
values
  ('service_name',   'resume-optimizer-api'),
  ('milestone',      'M3'),
  ('schema_version', 'm3_0001'),
  ('stack',          'Next.js 15 + FastAPI + LangGraph + Postgres')
on conflict (key) do update
  set value      = excluded.value,
      updated_at = now();

-- ------------------------------------------------------------ 本机用户
-- 项目是本地自托管的单机工具，不做账号体系：这个固定 UUID 就是最终形态。
insert into public.profiles (id, display_name, headline)
values (
  '00000000-0000-4000-8000-000000000001',
  '本地开发用户',
  '后端 / AI 应用开发'
)
on conflict (id) do update
  set display_name = excluded.display_name,
      headline     = excluded.headline;

-- -------------------------------------------------------- 示例经历（三条）
-- 垂直切片的输入素材，同时也是 M2 素材库页面的演示数据。
-- raw_description 是事实基线，改写必须可回溯到这里。
--
-- 刻意做成三种 kind 各一条：M2-5 的列表页要「按分类分组」，
-- 只有一条数据时分组逻辑看着是对的，其实没被验证过。
-- 时间上也刻意错开（进行中 / 已结束的前后两段），
-- 用来验证「组内按经历时间倒序」这条排序规则。
insert into public.experiences (
  id, user_id, kind, org, role, start_date, end_date,
  raw_description, skill_tags, highlights, metrics, variants, sort_order
)
values
  (
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
    -- 量化结果与定性要点分开放：改写时数字只许从这里取（M4-7）
    '[
      {"name": "后端单元测试", "value": "24 → 82 条", "context": "ruff + pytest，CI 全绿"},
      {"name": "API 接口数",   "value": "12 个 path", "context": "OpenAPI schema 统计"},
      {"name": "CI 耗时",      "value": "55 秒",      "context": "4 个 job 并行，GitHub Actions"}
    ]'::jsonb,
    -- 多版本表述：同一段经历，投不同方向时侧重点不同（M2-6）
    '[
      {
        "direction": "后端开发",
        "text": "用 FastAPI 承载全部业务逻辑，以 LangGraph 编排多步 LLM 流程；通过 OpenAPI 自动生成前端类型，从结构上消除前后端类型双写。",
        "note": "投后端岗时强调框架与工程化"
      },
      {
        "direction": "AI 应用",
        "text": "把「JD 解析 → 经历改写 → 评分回环」拆成可并行编排的图节点，并为每次 LLM 调用建立按 trace_id 回放的观测链路。",
        "note": "投 AI 应用岗时强调 agent 编排与可观测性"
      }
    ]'::jsonb,
    0
  ),
  (
    '00000000-0000-4000-8000-000000000102',
    '00000000-0000-4000-8000-000000000001',
    'internship',
    '某互联网公司',
    '后端开发实习生',
    date '2026-06-01',
    date '2026-08-31',
    '参与订单服务的接口开发与维护，负责部分接口的性能优化；'
    || '参与每周的线上问题排查，整理并修复了若干慢查询。',
    array['Python', 'PostgreSQL', 'Redis'],
    array['负责订单查询接口的重构与索引优化', '参与线上问题排查并整理成内部文档'],
    '[
      {"name": "订单查询接口 P99 延迟", "value": "800ms → 120ms", "context": "压测 5000 QPS 下"},
      {"name": "慢查询", "value": "7 条 → 0 条", "context": "按慢日志统计"}
    ]'::jsonb,
    '[
      {
        "direction": "后端开发",
        "text": "重构订单查询链路并重做索引设计，压测 5000 QPS 下 P99 从 800ms 降到 120ms；清理慢日志中 7 条高频慢查询。",
        "note": null
      }
    ]'::jsonb,
    1
  ),
  (
    '00000000-0000-4000-8000-000000000103',
    '00000000-0000-4000-8000-000000000001',
    'campus',
    '校计算机协会',
    '技术部负责人',
    date '2025-09-01',
    date '2026-06-30',
    '组织协会内部的技术分享与项目实践，负责新成员的入门培训安排。',
    array['组织协调', '技术分享'],
    array['组织了 6 场内部技术分享'],
    '[
      {"name": "内部技术分享", "value": "6 场", "context": "2025 秋季学期"}
    ]'::jsonb,
    '[]'::jsonb,
    2
  )
on conflict (id) do update
  set kind            = excluded.kind,
      org             = excluded.org,
      role            = excluded.role,
      start_date      = excluded.start_date,
      end_date        = excluded.end_date,
      raw_description = excluded.raw_description,
      skill_tags      = excluded.skill_tags,
      highlights      = excluded.highlights,
      metrics         = excluded.metrics,
      variants        = excluded.variants,
      sort_order      = excluded.sort_order;
