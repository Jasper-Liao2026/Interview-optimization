-- ============================================================
-- M0-5 · 初始化 migration
-- ============================================================
-- 这一步刻意只做「证明 migration 流程可用」所需的最小事情，
-- 不提前定义业务表 —— 用户 / 经历条目 / JD / 简历 是 M1-1 的活。
--
-- 幂等：全部使用 if not exists / drop ... if exists，可重复执行。

-- ---------- 扩展 ----------
-- vector 是 M3-4 做经历条目语义检索（pgvector）的前提，
-- 现在装上，避免 M3 阶段再来一次镜像与迁移的折腾。
create extension if not exists "vector";
-- gen_random_uuid() 依赖 pgcrypto
create extension if not exists "pgcrypto";

-- ---------- 自检元数据表 ----------
-- 这张表存在的意义是「可观测」：
-- /api/v1/system/info 会把它的内容读出来返回给前端，
-- 于是「前端能显示后端数据」这一现象，同时证明了
--   (a) 前端 → 后端 通
--   (b) 后端 → 数据库 通
--   (c) migration 真的作用到了这个库上
create table if not exists public.service_meta (
  key        text        primary key,
  value      text        not null,
  updated_at timestamptz not null default now()
);

comment on table public.service_meta is
  'M0 脚手架自检用的键值元数据。非业务表，M1-1 起可保留用于记录 schema 版本。';

-- ---------- RLS ----------
-- 从第一天就打开 RLS：M2-2 要做用户级隔离时，只需追加策略，
-- 不必回头处理「表已经裸奔了一段时间」的历史包袱。
alter table public.service_meta enable row level security;

-- 这里不写 `to anon, authenticated`：
-- 那些角色只在 Supabase 实例里存在，原生 postgres 镜像没有，
-- 写上会让本 migration 无法在 docker compose 的 postgres 上执行。
-- 用默认的 PUBLIC 兜底，M2-2 再接 Supabase 角色。
drop policy if exists service_meta_read_all on public.service_meta;
create policy service_meta_read_all
  on public.service_meta
  for select
  using (true);
