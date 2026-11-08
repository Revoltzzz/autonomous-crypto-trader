-- Respect the database safe-update guard when refreshing current positions.
begin;
create or replace function public.persist_paper_cycle(payload jsonb, ledger jsonb)
returns void language plpgsql security invoker set search_path = public as $$
declare cid uuid := (payload->>'cycle_id')::uuid; item jsonb; mint text;
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
 if ledger is not null then
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
   on conflict(id) do update set record=excluded.record;
  end loop;
 end if;
end $$;
commit;
