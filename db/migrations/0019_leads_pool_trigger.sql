-- 0019_leads_pool_trigger.sql — make leads.pool a guaranteed projection of
-- leads.status, enforced at write time. Before this, pool was only refreshed by
-- compute_leads; an out-of-band status edit (API PATCH, manual SQL) could leave
-- pool stale until the nightly reconcile. This trigger removes that window: pool
-- is ALWAYS pool_of_status(status), for every insert/update, from any code path.
-- Source of truth for the status set: crm/leads.py PROSPECT_STATUSES (keep in sync).
create or replace function leads_set_pool() returns trigger
language plpgsql as $$
begin
  new.pool := case
    when new.status in ('Replied','Meeting booked','Waiting for a reply','Qualified','Converted')
      then 'prospect' else 'suspect' end;
  return new;
end $$;

drop trigger if exists leads_pool_sync on leads;
create trigger leads_pool_sync
  before insert or update of status on leads
  for each row execute function leads_set_pool();

-- Reconcile any existing drift right now.
update leads set pool = case
  when status in ('Replied','Meeting booked','Waiting for a reply','Qualified','Converted')
    then 'prospect' else 'suspect' end
where pool is distinct from (case
  when status in ('Replied','Meeting booked','Waiting for a reply','Qualified','Converted')
    then 'prospect' else 'suspect' end);

notify pgrst, 'reload schema';
