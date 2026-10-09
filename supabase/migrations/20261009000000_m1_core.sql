-- ============================================================
-- M1-1 · 数据模型 v0（用户 / 经历条目 / JD / 简历）
-- ============================================================
-- 目标：让「一条经历 + 一个 JD → 生成 → 导出 PDF」这条垂直切片有地方落数据。
--
-- 设计原则（与 tasks.md 对齐）：
--   1. **最小可用，不追求完备**。M2-1 会补「多版本表述」「量化结果细分」等字段，
--      本 migration 只定义到「垂直切片跑得通」所需的程度。
--   2. **经历是结构化原子条目，不是一段文本**（M2 的核心资产）。
--      因此 org / role / 时间区间 / raw_description / skill_tags / highlights 分列存放，
--      而不是一个 markdown blob —— 后续 M3-4 向量化、M3-5 匹配都依赖这个粒度。
--   3. **RLS 从第一天就开**（延续 M0-5 的做法）：M2-2 只需追加策略，不必回头清理裸奔历史。
--
-- 幂等：可重复执行（if not exists / drop ... if exists / on conflict）。
-- 注意：docker compose 的 docker-entrypoint-initdb.d **不递归子目录**，
--       新增 migration 必须同步在 docker-compose.yml 里逐个挂载。

create extension if not exists "pgcrypto";

-- ------------------------------------------------ updated_at 统一维护
-- 五张业务表都有 updated_at，用一个触发器函数统一维护，
-- 避免每处 update 都要记得手写 set updated_at = now()（漏一次就是脏数据）。
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- ------------------------------------------------------------- profiles
-- 用户。本项目是本地自托管的单机工具，固定一个「本机用户」（见 seed.sql）。
create table if not exists public.profiles (
  id           uuid        primary key default gen_random_uuid(),
  display_name text        not null default '本地开发用户',
  headline     text        ,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

comment on table public.profiles is
  '用户档案。本地单机工具固定一个本机用户，不做账号体系。';

drop trigger if exists trg_profiles_updated_at on public.profiles;
create trigger trg_profiles_updated_at
  before update on public.profiles
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------- experiences
-- 经历条目 —— 本人产品的核心资产。
-- kind 用 text + check 而不是 enum：加一个分类只改约束，不必走 ALTER TYPE 迁移。
create table if not exists public.experiences (
  id              uuid        primary key default gen_random_uuid(),
  user_id         uuid        not null references public.profiles (id) on delete cascade,
  kind            text        not null
                              check (kind in ('project', 'internship', 'campus')),
  org             text        not null,
  role            text        not null,
  start_date      date        ,
  end_date        date        ,
  -- 原始描述：用户录入时的原话，**改写时必须可回溯到这里**（M4-7 产品红线）。
  raw_description text        not null,
  -- 技能标签与量化要点。用数组而非子表：M1 阶段读写都在一条记录内完成，查询也更简单；
  -- M2-1 若需要按标签检索/聚合，再迁到 join table。
  skill_tags      text[]      not null default '{}',
  highlights      text[]      not null default '{}',
  sort_order      integer     not null default 0,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  constraint experiences_date_order check (
    end_date is null or start_date is null or end_date >= start_date
  )
);

comment on table public.experiences is
  '结构化经历条目（项目 / 实习 / 校园）。raw_description 是事实基线，改写内容必须可回溯。';
comment on column public.experiences.highlights is
  '量化结果 / 要点，供改写时引用；不可由模型凭空生成（M4-7）。';

create index if not exists idx_experiences_user_sort
  on public.experiences (user_id, kind, sort_order);

drop trigger if exists trg_experiences_updated_at on public.experiences;
create trigger trg_experiences_updated_at
  before update on public.experiences
  for each row execute function public.set_updated_at();

-- ------------------------------------------------------ job_descriptions
create table if not exists public.job_descriptions (
  id           uuid        primary key default gen_random_uuid(),
  user_id      uuid        not null references public.profiles (id) on delete cascade,
  title        text        ,
  company      text        ,
  raw_text     text        not null,
  -- 结构化岗位画像（M1-3 的 LLM 输出）。用 jsonb 而非列：
  -- 画像的字段集合还在演化（M3-1 会加「隐性偏好」等），jsonb 免去每加一个字段就迁移一次。
  -- 代价是失去列级类型约束 —— 用 Pydantic 在写入前校验兜住。
  parsed       jsonb       ,
  parser_model text        ,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

comment on table public.job_descriptions is
  'JD 原文 + 结构化画像。parsed 的形状由 apps/api 的 JobProfile 定义并校验。';

create index if not exists idx_job_descriptions_user
  on public.job_descriptions (user_id, created_at desc);

drop trigger if exists trg_job_descriptions_updated_at on public.job_descriptions;
create trigger trg_job_descriptions_updated_at
  before update on public.job_descriptions
  for each row execute function public.set_updated_at();

-- --------------------------------------------------------------- resumes
-- M1 里 resume 是「一次生成的快照」，结构直接以 jsonb 存下，
-- 因为 M1-5 的渲染只需要读它，M6 做编辑页时再考虑拆表。
create table if not exists public.resumes (
  id            uuid        primary key default gen_random_uuid(),
  user_id       uuid        not null references public.profiles (id) on delete cascade,
  jd_id         uuid        references public.job_descriptions (id) on delete set null,
  title         text        not null,
  template      text        not null default 'classic',
  -- 抬头（姓名 / 一句话定位）也快照进来，而不是每次渲染都去 join profiles：
  -- 简历是**某一时刻的产物**，之后改了 profile 不应回溯改变历史简历。
  header        jsonb       not null default '{}'::jsonb,
  sections      jsonb       not null default '[]'::jsonb,
  status        text        not null default 'draft'
                            check (status in ('draft', 'exported')),
  -- 生成时的模型标识，便于回溯「这份简历是哪版 prompt / 哪个模型产出的」
  generator     text        ,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

comment on table public.resumes is
  '一次生成的简历快照。header 与 sections 均为快照，形状由 Pydantic 定义。';

create index if not exists idx_resumes_user
  on public.resumes (user_id, created_at desc);

drop trigger if exists trg_resumes_updated_at on public.resumes;
create trigger trg_resumes_updated_at
  before update on public.resumes
  for each row execute function public.set_updated_at();

-- ------------------------------------------------------------------ RLS
-- 全部开启，但**只加读策略**：
--   - 后端以 postgres（表 owner + 超级用户）连接，默认绕过 RLS，因此写路径不受影响；
--   - 单机单用户场景用不上隔离，但打开它零成本，将来真要支持多用户时
--     只需追加 insert/update/delete 策略，不必回头补历史包袱。
-- 策略不写 `to anon, authenticated`：本项目的库是原生 pgvector 镜像，
-- 那两个角色并不存在，写了会让 migration 执行失败。
do $$
declare
  t text;
begin
  foreach t in array array[
    'profiles', 'experiences', 'job_descriptions', 'resumes'
  ]
  loop
    execute format('alter table public.%I enable row level security', t);
    execute format('drop policy if exists %I_read_all on public.%I', t, t);
    execute format(
      'create policy %I_read_all on public.%I for select using (true)', t, t
    );
  end loop;
end;
$$;

-- ------------------------------------------------------- schema 版本标记
-- 让 /api/v1/system/info 能直观显示「库已经升到 M1」。
insert into public.service_meta (key, value)
values
  ('schema_version', 'm1_0001'),
  ('milestone',      'M1')
on conflict (key) do update
  set value      = excluded.value,
      updated_at = now();
