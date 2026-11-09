-- Paper decisions and append-only history, atomically persisted with each cycle.
begin;
create table public.paper_decisions (
 id uuid primary key,
 cycle_id uuid not null references public.cycles(id),
 mint_address text not null, symbol text, risk_score double precision,
 decision text not null check(decision in ('BUY','REJECT','WATCH')),
 reasoning text not null, proposal jsonb,
 created_at timestamptz not null default now()
);
create index paper_decisions_cycle_idx on public.paper_decisions(cycle_id);
alter table public.paper_decisions enable row level security;
revoke all on public.paper_decisions from public, anon, authenticated;
grant all on public.paper_decisions to service_role;

create or replace function public.persist_paper_cycle(payload jsonb, ledger jsonb)
returns void language plpgsql security invoker set search_path = public as $$
declare cid uuid := (payload->>'cycle_id')::uuid; item jsonb; mint text;
 old_history jsonb;
begin
 if payload->>'paper_only' is distinct from 'true' then
  raise exception 'Paper-only reports required';
 end if;
 perform pg_advisory_xact_lock(732001);
 insert into cycles values (cid, (payload->>'started_at')::timestamptz,
  (payload->>'finished_at')::timestamptz, payload->>'status', payload)
 on conflict(id) do update set completed_at=excluded.completed_at,
  status=excluded.status, report=excluded.report;
 delete from market_snapshots where cycle_id=cid;
 for item in select value from jsonb_array_elements(payload->'markets') loop
  insert into market_snapshots select * from jsonb_populate_record(
   null::market_snapshots, item || jsonb_build_object('cycle_id',cid));
 end loop;
 delete from token_discoveries where cycle_id=cid;
 for item in select value from jsonb_array_elements(payload->'discoveries') loop
  insert into token_discoveries select * from jsonb_populate_record(
   null::token_discoveries, item || jsonb_build_object('cycle_id',cid));
 end loop;
 delete from security_assessments where cycle_id=cid;
 for mint, item in select key,value from jsonb_each(payload->'assessments') loop
  insert into security_assessments select * from jsonb_populate_record(
   null::security_assessments,
   item || jsonb_build_object('cycle_id',cid,'mint_address',mint));
 end loop;
 for item in select value from jsonb_array_elements(coalesce(payload->'decisions','[]'::jsonb)) loop
  if item->>'cycle_id' is distinct from cid::text then
   raise exception 'Decision cycle mismatch';
  end if;
  insert into paper_decisions select * from jsonb_populate_record(null::paper_decisions,item)
   on conflict(id) do nothing;
 end loop;
 if ledger is not null then
  select history into old_history from paper_ledger_state where id='paper-main';
  if old_history is not null and (
    jsonb_array_length(ledger->'history') < jsonb_array_length(old_history)
    or exists (select 1 from jsonb_array_elements(old_history) with ordinality as e(value,n)
     where (ledger->'history')->(n::int-1) is distinct from value)) then
   raise exception 'Paper ledger history must be append-only';
  end if;
  insert into paper_ledger_state(id,history,account) values
   ('paper-main',ledger->'history',ledger->'account')
  on conflict(id) do update set history=excluded.history, account=excluded.account,
   updated_at=now();
  delete from paper_positions where id is not null;
  for item in select value from jsonb_array_elements(ledger->'positions') loop
   insert into paper_positions values ((item->>'id')::uuid,item);
  end loop;
  for item in select value from jsonb_array_elements(ledger->'trades') loop
   insert into paper_trades values ((item->>'id')::uuid,item)
   on conflict(id) do nothing;
  end loop;
 end if;
end $$;
revoke all on function public.persist_paper_cycle(jsonb,jsonb) from public, anon, authenticated;
grant execute on function public.persist_paper_cycle(jsonb,jsonb) to service_role;
commit;
