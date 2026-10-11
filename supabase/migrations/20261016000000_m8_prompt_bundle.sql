-- M8.1: retain the complete immutable prompt bundle for persisted versions.
-- Existing rows keep their historical content_hash; the application backfills
-- snapshot on the next ensure_shipped/save_prompt_snapshots call.
alter table public.prompt_versions
  add column if not exists snapshot jsonb;

insert into public.service_meta(key,value) values ('schema_version','m8_0002'),('milestone','M8')
on conflict(key) do update set value=excluded.value,updated_at=now();
