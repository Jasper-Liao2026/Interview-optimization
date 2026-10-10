-- M3: 视觉 JD 来源 + 独立向量缓存。维度固定 1536，HNSW 支持余弦检索。
create extension if not exists vector;
alter table public.job_descriptions add column if not exists source_type text not null default 'text'
  check (source_type in ('text', 'image'));

create table if not exists public.experience_embeddings (
  experience_id uuid primary key references public.experiences(id) on delete cascade,
  source_hash text not null,
  model_key text not null,
  embedding vector(1536) not null,
  created_at timestamptz not null default now()
);
create index if not exists idx_experience_embeddings_cosine
  on public.experience_embeddings using hnsw (embedding vector_cosine_ops);
alter table public.experience_embeddings enable row level security;

-- PUT 改了事实字段即失效；variants、时间、排序不作为事实匹配来源。
create or replace function public.invalidate_experience_embedding() returns trigger
language plpgsql as $$
begin
  if (new.org, new.role, new.raw_description, new.skill_tags, new.highlights, new.metrics)
     is distinct from
     (old.org, old.role, old.raw_description, old.skill_tags, old.highlights, old.metrics) then
    delete from public.experience_embeddings where experience_id = new.id;
  end if;
  return new;
end;
$$;
drop trigger if exists trg_experience_embedding_invalidate on public.experiences;
create trigger trg_experience_embedding_invalidate after update on public.experiences
  for each row execute function public.invalidate_experience_embedding();

insert into public.service_meta(key, value) values ('schema_version', 'm3_0001'), ('milestone', 'M3')
on conflict(key) do update set value=excluded.value, updated_at=now();
