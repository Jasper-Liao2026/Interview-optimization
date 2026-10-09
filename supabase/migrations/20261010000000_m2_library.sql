-- ============================================================
-- M2 · 素材库（M2-1 完整经历模型 / M2-6 多版本表述）
-- ============================================================
-- 目标：把「结构化原子条目」补到能承接真实录入与后续匹配的粒度。
--
-- 本 migration 只做**加列**，不动已有列、不重命名、不删数据 ——
-- 因为 M1 的 seed 与运行中的库都已经有数据，破坏性变更不值得。
--
-- 两处新增：
--   1. `metrics`  —— 量化结果细分（M2-1）
--   2. `variants` —— 多版本表述（M2-6）
-- 另有 `sort_order` 的语义降级说明，见文末注释。
--
-- 幂等：可重复执行（add column if not exists / on conflict）。

-- --------------------------------------------------------- 1. 量化结果
-- 为什么把「量化结果」从 highlights 里拆出来：
--   M4-7 的红线是「改写不得编造数字」。数字与定性文字混在一个文本数组里时，
--   事后只能做字符串比对；拆成 (指标名, 数值, 口径) 之后，
--   「改写文本里出现的每个数字都能在 metrics 里找到来源」就是一条可自动化的校验。
--   同时 M3-5 的条目-要求匹配可以拿指标名去对 JD 的「高并发 / 性能优化」这类要求。
--
-- 用 jsonb 而不是子表，与 job_descriptions.parsed 的取舍一致：
--   形状还在演化（将来可能加 trend / period），且只在条目内读写，不做跨条目聚合查询。
--   代价是失去列级约束 —— 由 Pydantic 在写入前校验兜住（见 ExperienceMetric）。
alter table public.experiences
  add column if not exists metrics jsonb not null default '[]'::jsonb;

comment on column public.experiences.metrics is
  '量化结果：[{name: 指标名, value: 数值原文, context: 口径}]。'
  '改写只允许引用其中的数字，不允许模型凭空生成（M4-7）。形状由 ExperienceMetric 定义。';

-- ------------------------------------------------------- 2. 多版本表述
-- 「同一条目按岗位方向保存多份表述」—— 素材库越用越值钱的关键设计。
--
-- 这些变体是**派生内容**，不是事实源：事实源始终是 raw_description + highlights + metrics。
-- M2 阶段只做「存下来 + 展示」（生成是 M4 的事），变体也不参与跨条目检索，
-- 因此用条目行内的 jsonb 而不是 1:N 子表：
--   - 素材库列表页一次查询拿全，不需要 join
--   - 写入在一个事务内完成，仓储只多一次 update
-- 若 M5 真要「按变体打分 / 按方向检索」，那时再迁到 experience_variants 子表，
-- 迁移成本就是一次数据搬运。
--
-- 唯一性约束（同一条目同方向只有一份表述）在 Pydantic 层校验，不在这里做 ——
-- jsonb 内的唯一约束要靠表达式索引，收益不抵复杂度。
alter table public.experiences
  add column if not exists variants jsonb not null default '[]'::jsonb;

comment on column public.experiences.variants is
  '按岗位方向保存的表述版本：[{direction, text, note}]。派生内容，'
  '事实基线仍是 raw_description。形状由 ExperienceVariant 定义，同方向唯一。';

-- ------------------------------------------------------- 3. 排序语义变更
-- M2 起列表不再让用户手工排序（M2-5 的验收只要求「分类 + 排序」）：
--   列表按 **kind 分组 → 组内按经历时间倒序**（进行中排最前）。
-- 因此 `sort_order` 降级为「同一时间的稳定排序兜底」，UI 不再暴露。
-- 列**刻意保留**：一是删列要动 M1 的 seed 与仓储，二是 M6/M7 若要「手动指定
-- 简历里经历的先后」时可以原地启用，不必再走一次 migration。
comment on column public.experiences.sort_order is
  '同一分类内的展示顺序。M2 起 UI 不再暴露（列表按经历时间倒序），'
  '仅作为同时间条目的稳定排序兜底；M6/M7 需要显式排序时可重新启用。';

-- 组内按时间倒序需要 (user_id, kind, end_date desc) 这类索引才走得顺。
-- 现有 idx_experiences_user_sort 仍留给 sort_order 兜底路径，这里补一个时间序索引。
create index if not exists idx_experiences_user_kind_time
  on public.experiences (user_id, kind, end_date desc nulls first, start_date desc nulls last);

-- ------------------------------------------------------- 4. schema 版本标记
-- 让 /api/v1/system/info 能直观显示「库已经升到 M2」。
insert into public.service_meta (key, value)
values
  ('schema_version', 'm2_0001'),
  ('milestone',      'M2')
on conflict (key) do update
  set value      = excluded.value,
      updated_at = now();
