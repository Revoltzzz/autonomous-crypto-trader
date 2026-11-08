-- Paper-only research persistence. Apply as a single transaction.
begin;
create table public.cycles (
 id uuid primary key, started_at timestamptz not null,
 completed_at timestamptz not null, status text not null,
 report jsonb not null
);
create table public.market_snapshots (
 cycle_id uuid references public.cycles(id), symbol text,
 price_usd double precision, market_cap_usd double precision,
 volume_24h_usd double precision, change_24h_pct double precision,
 fetched_at timestamptz, source text, primary key(cycle_id, symbol)
);
create table public.token_discoveries (
 cycle_id uuid references public.cycles(id), name text, symbol text,
 mint_address text, pool_address text, first_seen_at timestamptz,
 price_usd double precision, liquidity_usd double precision,
 volume_24h_usd double precision, buys_24h bigint, sells_24h bigint,
 source_url text, unverified_fields jsonb,
 primary key(cycle_id, pool_address)
);
create table public.security_assessments (
 cycle_id uuid references public.cycles(id), mint_address text,
 mint_authority_revoked boolean, freeze_authority_revoked boolean,
 lp_locked_pct double precision, top_holder_concentration_pct double precision,
 risks jsonb, score double precision, source text,
 primary key(cycle_id, mint_address)
);
-- Full validated Pydantic records are retained without lossy field conversion.
create table public.paper_positions (id uuid primary key, record jsonb not null);
create table public.paper_trades (id uuid primary key, record jsonb not null);
create table public.paper_ledger_state (
 id text primary key check(id = 'paper-main'),
 history jsonb not null check(jsonb_typeof(history) = 'array'),
 account jsonb not null, updated_at timestamptz not null default now()
);
alter table public.cycles enable row level security;
alter table public.market_snapshots enable row level security;
alter table public.token_discoveries enable row level security;
alter table public.security_assessments enable row level security;
alter table public.paper_positions enable row level security;
alter table public.paper_trades enable row level security;
alter table public.paper_ledger_state enable row level security;
revoke all on public.cycles, public.market_snapshots, public.token_discoveries,
 public.security_assessments, public.paper_positions, public.paper_trades,
 public.paper_ledger_state from anon, authenticated;
grant all on public.cycles, public.market_snapshots, public.token_discoveries,
 public.security_assessments, public.paper_positions, public.paper_trades,
 public.paper_ledger_state to service_role;

-- One REST RPC commits a cycle and its authoritative ledger atomically.
-- No public policies; only the backend service role may call this function.
create function public.persist_paper_cycle(payload jsonb, ledger jsonb)
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
  delete from paper_positions;
  for item in select value from jsonb_array_elements(ledger->'positions') loop
   insert into paper_positions values ((item->>'id')::uuid,item);
  end loop;
  for item in select value from jsonb_array_elements(ledger->'trades') loop
   insert into paper_trades values ((item->>'id')::uuid,item)
   on conflict(id) do update set record=excluded.record;
  end loop;
 end if;
end $$;
revoke all on function public.persist_paper_cycle(jsonb,jsonb) from public, anon, authenticated;
grant execute on function public.persist_paper_cycle(jsonb,jsonb) to service_role;
commit;
