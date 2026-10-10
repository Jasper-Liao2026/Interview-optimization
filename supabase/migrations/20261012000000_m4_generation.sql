-- M4 run metadata; LangGraph owns its checkpoint tables through saver.setup().
create table if not exists public.generation_runs (
  id uuid primary key,
  user_id uuid not null references public.profiles(id) on delete cascade,
  payload jsonb not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists idx_generation_runs_user on public.generation_runs(user_id,updated_at desc);
alter table public.generation_runs enable row level security;
insert into public.service_meta(key,value) values ('schema_version','m4_0001'),('milestone','M4')
on conflict(key) do update set value=excluded.value,updated_at=now();
