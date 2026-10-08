-- ============================================================
-- 本地开发种子数据
-- ============================================================
-- 幂等：可重复执行（suppress 冲突时更新）。
-- `supabase db reset` 会自动应用 migrations + seed.sql；
-- docker compose 首次初始化时也会执行本文件。

insert into public.service_meta (key, value)
values
  ('service_name',   'resume-optimizer-api'),
  ('milestone',      'M0'),
  ('schema_version', 'm0_0001'),
  ('stack',          'Next.js 15 + FastAPI + LangGraph + Supabase')
on conflict (key) do update
  set value      = excluded.value,
      updated_at = now();
