-- M8-2/3/4: immutable prompt snapshots, golden datasets and comparisons.
create table if not exists public.prompt_versions (
  version text primary key,
  jd_system text not null,
  rewrite_system text not null,
  jd_image text not null,
  content_hash text not null,
  created_at timestamptz not null default now()
);

create table if not exists public.prompt_settings (
  key text primary key,
  selected_version text not null references public.prompt_versions(version),
  previous_version text references public.prompt_versions(version),
  changed_by text not null default 'system',
  changed_at timestamptz not null default now()
);

create table if not exists public.evaluation_datasets (
  id uuid primary key,
  version text not null unique,
  synthetic boolean not null default true,
  cases jsonb not null,
  created_at timestamptz not null default now()
);

create table if not exists public.evaluation_runs (
  id uuid primary key,
  dataset_id uuid not null references public.evaluation_datasets(id) on delete restrict,
  prompt_a text not null,
  prompt_b text not null,
  mode text not null check (mode in ('stub', 'real')),
  result jsonb not null,
  created_at timestamptz not null default now()
);
create index if not exists evaluation_runs_dataset_idx on public.evaluation_runs(dataset_id, created_at desc);

create table if not exists public.generation_llm_calls (
  id uuid primary key,
  run_id uuid not null references public.generation_runs(id) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade,
  payload jsonb not null,
  created_at timestamptz not null default now()
);
create index if not exists generation_llm_calls_run on public.generation_llm_calls(user_id,run_id);

alter table public.prompt_versions enable row level security;
alter table public.evaluation_datasets enable row level security;
alter table public.evaluation_runs enable row level security;
alter table public.generation_llm_calls enable row level security;

insert into public.service_meta(key,value) values ('schema_version','m8_0001'),('milestone','M8')
on conflict(key) do update set value=excluded.value,updated_at=now();
