-- 0030_state_machine.sql — Phase 1 of the master plan: leads.status is the ONLY
-- writable lifecycle field; everything else is a projection.
--
--   leads.pool            <- leads_set_pool (existed; unchanged here)
--   leads.lifecycle_stage <- NEW: same BEFORE trigger, deterministic map of status
--   contacts.status       <- sync_lead_tag rewritten as the COMPLETE projection:
--                              converted lead -> 'customer'
--                              active lead    -> 'lead'
--                              no active lead -> demote 'lead' back to 'contact'
--   'lead' tag            <- sync_lead_tag (existed; kept)
--
-- The Maya Feuer class (five fields disagreeing) dies here: scripts stop writing
-- contacts.status / lifecycle_stage (Phase 1 code change), and even if one does,
-- the projection wins on the next lead write and the nightly reconciler reports it.

-- ── pool + lifecycle: one BEFORE trigger on leads ─────────────────────────────
create or replace function leads_set_pool() returns trigger
language plpgsql as $$
begin
  new.pool := case
    when new.status in ('Replied','Meeting booked','Waiting for a reply','Qualified','Nurture','Converted')
      then 'prospect' else 'suspect' end;
  -- lifecycle_stage is a pure function of status (classify_revops no longer writes it)
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

-- ── contacts.status: complete projection on any leads change ─────────────────
create or replace function sync_lead_tag() returns trigger
language plpgsql as $$
declare cid uuid; active boolean; converted boolean;
begin
  cid := coalesce(NEW.contact_id, OLD.contact_id);
  if cid is null then return coalesce(NEW, OLD); end if;
  select
    exists(select 1 from leads where contact_id=cid
           and lower(coalesce(status,'')) not in ('converted','unqualified')),
    exists(select 1 from leads where contact_id=cid
           and (converted_opportunity_id is not null or status='Converted'))
    into active, converted;

  if converted then
    update contacts set status='customer', updated_at=now()
      where id=cid and status is distinct from 'customer';
  elsif active then
    update contacts set status='lead', updated_at=now()
      where id=cid and archived is not true and status not in ('customer','lead');
  else
    -- no active/converted lead: a 'lead' status is stale -> back to plain contact
    update contacts set status='contact', updated_at=now()
      where id=cid and status = 'lead';
  end if;

  -- 'lead' tag mirrors the active flag (as before)
  if active then
    update contacts set tags=(select array_agg(distinct t) from unnest(array_append(coalesce(tags,'{}'),'lead')) t)
      where id=cid and not (coalesce(tags,'{}') @> array['lead']);
  else
    update contacts set tags=array_remove(tags,'lead')
      where id=cid and coalesce(tags,'{}') @> array['lead'];
  end if;
  return coalesce(NEW, OLD);
end $$;

-- ── backfill both projections over existing rows ──────────────────────────────
update leads set lifecycle_stage = case
    when converted_opportunity_id is not null or status = 'Converted' then 'Customer'
    when status = 'Meeting booked'                                    then 'Meeting'
    when status = 'Qualified'                                         then 'Qualified'
    when status in ('Replied','Waiting for a reply')                  then 'Engaged'
    when status = 'Nurture'                                           then 'Nurture'
    when status = 'Unqualified'                                       then null
    else 'Lead' end
  where lifecycle_stage is distinct from (case
    when converted_opportunity_id is not null or status = 'Converted' then 'Customer'
    when status = 'Meeting booked'                                    then 'Meeting'
    when status = 'Qualified'                                         then 'Qualified'
    when status in ('Replied','Waiting for a reply')                  then 'Engaged'
    when status = 'Nurture'                                           then 'Nurture'
    when status = 'Unqualified'                                       then null
    else 'Lead' end);

update contacts c set status='customer', updated_at=now()
  where exists (select 1 from leads l where l.contact_id=c.id
                and (l.converted_opportunity_id is not null or l.status='Converted'))
    and c.status is distinct from 'customer';

notify pgrst, 'reload schema';
