-- Scoring history and a separately exportable best resume, written atomically.
alter table public.resumes add column if not exists generator_vendor text;
create table if not exists public.resume_score_runs (
  id uuid primary key,
  user_id uuid not null references public.profiles(id) on delete cascade,
  source_resume_id uuid not null references public.resumes(id) on delete cascade,
  best_resume_id uuid not null references public.resumes(id) on delete cascade,
  result jsonb not null,
  created_at timestamptz not null default now()
);
create index if not exists resume_score_runs_source_idx
  on public.resume_score_runs(user_id, source_resume_id, created_at desc);
alter table public.resume_score_runs enable row level security;
-- Local single-user service uses the database owner; API queries always bind user_id.
insert into public.service_meta(key,value) values ('schema_version','m5_0001'),('milestone','M5')
on conflict(key) do update set value=excluded.value,updated_at=now();
