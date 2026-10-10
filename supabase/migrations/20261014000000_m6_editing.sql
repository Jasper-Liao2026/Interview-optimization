-- Editor revision counter and immutable snapshots. Existing resumes start at revision 0.
alter table public.resumes add column if not exists edit_revision integer not null default 0;

-- Preserve existing M5 best resumes before the editor can change them.
update public.resume_score_runs as scores
set result = jsonb_set(scores.result, '{resume_snapshot}', to_jsonb(resume), true)
from public.resumes as resume
where resume.id = scores.best_resume_id and resume.user_id = scores.user_id
  and not (scores.result ? 'resume_snapshot');

create table if not exists public.resume_revisions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  resume_id uuid not null references public.resumes(id) on delete cascade,
  revision integer not null check (revision >= 0),
  reason text not null,
  draft jsonb not null,
  provenance jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique (resume_id, revision)
);
create index if not exists resume_revisions_owner on public.resume_revisions(user_id,resume_id);

create table if not exists public.resume_edit_runs (
  id uuid primary key,
  user_id uuid not null references public.profiles(id) on delete cascade,
  resume_id uuid not null references public.resumes(id) on delete cascade,
  payload jsonb not null,
  updated_at timestamptz not null default now()
);
create index if not exists resume_edit_runs_owner on public.resume_edit_runs(user_id,resume_id);
