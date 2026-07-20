-- 0031_converted_terminal.sql — conversion is a TERMINAL state.
--
-- Bug (Eyal Amzaleg / NovaTaste): Convert set leads.status='Converted' but did
-- not pin it, so the nightly compute_leads re-derived status from interactions
-- ("has a meeting -> Meeting booked") and dragged a converted lead backwards.
-- The ★ badge (converted_opportunity_id) and the board group (status) disagreed.
--
-- Fix at the strongest layer: the BEFORE trigger forces status='Converted'
-- whenever converted_opportunity_id is set - no writer can regress it. To
-- un-convert, clear converted_opportunity_id first (a deliberate act).
create or replace function leads_set_pool() returns trigger
language plpgsql as $$
begin
  -- terminal: a lead attached to a deal is Converted, whatever a writer says
  if new.converted_opportunity_id is not null then
    new.status := 'Converted';
  end if;
  new.pool := case
    when new.status in ('Replied','Meeting booked','Waiting for a reply','Qualified','Nurture','Converted')
      then 'prospect' else 'suspect' end;
  new.lifecycle_stage := case
    when new.converted_opportunity_id is not null or new.status = 'Converted' then 'Customer'
    when new.status = 'Meeting booked'                                        then 'Meeting'
    when new.status = 'Qualified'                                             then 'Qualified'
    when new.status in ('Replied','Waiting for a reply')                      then 'Engaged'
    when new.status = 'Nurture'                                               then 'Nurture'
    when new.status = 'Unqualified'                                           then null
    else 'Lead' end;
  return new;
end $$;

-- The trigger only fires on status writes; also fire when the conversion itself
-- lands (insert or update of converted_opportunity_id).
drop trigger if exists leads_pool_sync on leads;
create trigger leads_pool_sync
  before insert or update of status, converted_opportunity_id on leads
  for each row execute function leads_set_pool();

-- Repair the stuck rows (fires the trigger, which forces Converted).
update leads set status = status
  where converted_opportunity_id is not null and status is distinct from 'Converted';

notify pgrst, 'reload schema';
