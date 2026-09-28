-- INFRA-002: Supabase exposes the `public` schema through its Data API (PostgREST) to anyone
-- holding the project's anon key. The agent is the single writer and connects as the table
-- owner (postgres), which bypasses RLS; so enabling RLS with NO policies denies anon /
-- authenticated roles everything and changes nothing for the agent. Harmless on plain Postgres.
-- New tables in later migrations must `enable row level security` themselves.

do $$
declare t text;
begin
  for t in select tablename from pg_tables where schemaname = 'public' loop
    execute format('alter table public.%I enable row level security', t);
  end loop;
end $$;
