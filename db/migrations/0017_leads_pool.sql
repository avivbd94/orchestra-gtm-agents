-- 0017_leads_pool.sql — split the lead pool into audience (suspect) vs pipeline
-- (prospect). pool is a pure projection of status; see crm/leads.pool_of_status.
alter table leads add column if not exists pool text not null default 'suspect';

-- Backfill from the current status (same rule as pool_of_status).
update leads set pool = case
  when status in ('Replied','Meeting booked','Waiting for a reply','Qualified','Converted')
    then 'prospect' else 'suspect' end;

do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'leads_pool_chk') then
    alter table leads add constraint leads_pool_chk check (pool in ('suspect','prospect'));
  end if;
end $$;

create index if not exists leads_pool_idx on leads(pool);
notify pgrst, 'reload schema';
