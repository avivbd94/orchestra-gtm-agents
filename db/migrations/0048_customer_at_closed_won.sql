-- 0048_customer_at_closed_won.sql — customer means Closed Won, nothing else.
--
-- Bug (Dor Levy / Jeen.ai, 2026-07-21): creating a deal set the lead Converted
-- (0031, correct) and the projections mapped Converted -> contacts.status
-- 'customer' + lifecycle 'Customer' unconditionally (0030). Result: 17
-- "customers" with exactly one Closed-Won deal in the system — and the one real
-- customer (a deal with no lead link) was NOT marked. An open deal is pipeline,
-- not a customer. Aviv: "deal שלא הפך ל-closed won - זה אומר שהוא לא לקוח".
--
-- Design: 0031 is untouched — Converted still means "this lead went to the
-- deals pipeline" and stays terminal. What changes is what Converted PROJECTS:
--   lead linked to a deal that is...   lifecycle        contacts.status
--     Closed Won                       Customer         customer
--     open (anything not Closed *)     Opportunity      lead
--     Closed Lost                      null             contact (demoted)
--     missing/unresolvable             Opportunity      lead
-- 'customer' additionally fires for ANY contact holding a Closed-Won deal via
-- opportunities.contact_id, lead or no lead (the Yael Schiller path).
-- The 'lead' tag keeps its old meaning: active pre-deal pipeline only.
--
-- A new trigger on opportunities re-fires the projection when a deal's stage
-- changes, so moving a deal to Closed Won flips the contact by itself.

-- ── pool + lifecycle: deal-aware lifecycle ───────────────────────────────────
create or replace function leads_set_pool() returns trigger
language plpgsql as $$
declare ostage text;
begin
  new.pool := case
    when new.status in ('Replied','Meeting booked','Waiting for a reply','Qualified','Nurture','Converted')
      then 'prospect' else 'suspect' end;
  if new.converted_opportunity_id is not null or new.status = 'Converted' then
    select stage into ostage from opportunities where id = new.converted_opportunity_id;
    new.lifecycle_stage := case
      when ostage = 'Closed Won'  then 'Customer'
      when ostage = 'Closed Lost' then null
      else 'Opportunity' end;   -- open deal, or Converted with no resolvable deal
  else
    new.lifecycle_stage := case
      when new.status = 'Meeting booked'                   then 'Meeting'
      when new.status = 'Qualified'                        then 'Qualified'
      when new.status in ('Replied','Waiting for a reply') then 'Engaged'
      when new.status = 'Nurture'                          then 'Nurture'
      when new.status = 'Unqualified'                      then null
      else 'Lead' end;
  end if;
  return new;
end $$;

-- ── contacts.status: customer iff Closed Won ─────────────────────────────────
create or replace function sync_lead_tag() returns trigger
language plpgsql as $$
declare cid uuid; won boolean; active boolean; in_deal boolean;
begin
  cid := coalesce(NEW.contact_id, OLD.contact_id);
  if cid is null then return coalesce(NEW, OLD); end if;
  select
    exists(select 1 from opportunities o where o.contact_id = cid and o.stage = 'Closed Won')
    or exists(select 1 from leads l join opportunities o on o.id = l.converted_opportunity_id
              where l.contact_id = cid and o.stage = 'Closed Won'),
    exists(select 1 from leads where contact_id = cid
           and lower(coalesce(status,'')) not in ('converted','unqualified')),
    exists(select 1 from leads l join opportunities o on o.id = l.converted_opportunity_id
           where l.contact_id = cid and o.stage not in ('Closed Won','Closed Lost'))
    into won, active, in_deal;

  if won then
    update contacts set status='customer', updated_at=now()
      where id=cid and status is distinct from 'customer';
  elsif active or in_deal then
    -- overwrites a stale 'customer' too: not won => not a customer
    update contacts set status='lead', updated_at=now()
      where id=cid and archived is not true and status is distinct from 'lead';
  else
    -- no pipeline, no won deal: demote projection-owned statuses only
    update contacts set status='contact', updated_at=now()
      where id=cid and status in ('lead','customer');
  end if;

  -- 'lead' tag mirrors ACTIVE (pre-deal pipeline) — semantics unchanged
  if active then
    update contacts set tags=(select array_agg(distinct t) from unnest(array_append(coalesce(tags,'{}'),'lead')) t)
      where id=cid and not (coalesce(tags,'{}') @> array['lead']);
  else
    update contacts set tags=array_remove(tags,'lead')
      where id=cid and coalesce(tags,'{}') @> array['lead'];
  end if;
  return coalesce(NEW, OLD);
end $$;

-- ── deals drive the projection too ───────────────────────────────────────────
create or replace function opportunities_reproject_contact() returns trigger
language plpgsql as $$
declare cid uuid; oid uuid;
begin
  cid := coalesce(NEW.contact_id, OLD.contact_id);
  oid := coalesce(NEW.id, OLD.id);
  -- touch every lead that can see this deal: re-fires both projections
  update leads set status = status
    where converted_opportunity_id = oid
       or (cid is not null and contact_id = cid);
  -- contacts with no lead rows at all (deal created directly on the contact)
  if cid is not null and not exists (select 1 from leads where contact_id = cid) then
    if exists (select 1 from opportunities o where o.contact_id = cid and o.stage = 'Closed Won') then
      update contacts set status='customer', updated_at=now()
        where id = cid and status is distinct from 'customer';
    else
      update contacts set status='contact', updated_at=now()
        where id = cid and status = 'customer';
    end if;
  end if;
  return coalesce(NEW, OLD);
end $$;

drop trigger if exists trg_opportunities_reproject on opportunities;
create trigger trg_opportunities_reproject
  after insert or update of stage or delete on opportunities
  for each row execute function opportunities_reproject_contact();

-- ── backfill: re-derive every row the new rules touch ────────────────────────
-- leads linked to a deal (fires lifecycle + contact projection)
update leads set status = status where converted_opportunity_id is not null;
-- leads of any contact that holds a deal (covers unlinked-deal contacts w/ leads)
update leads l set status = l.status
  from opportunities o where o.contact_id = l.contact_id;
-- every CURRENT customer that has any lead row: re-project (catches stale
-- customers whose leads have no deal at all - they must demote too)
update leads l set status = l.status
  from contacts c where c.id = l.contact_id and c.status = 'customer';
-- contacts with deals but NO leads: apply the rule directly
update contacts c set status='customer', updated_at=now()
  where exists (select 1 from opportunities o where o.contact_id=c.id and o.stage='Closed Won')
    and not exists (select 1 from leads l where l.contact_id=c.id)
    and c.status is distinct from 'customer';
update contacts c set status='contact', updated_at=now()
  where c.status='customer'
    and not exists (select 1 from opportunities o where o.contact_id=c.id and o.stage='Closed Won')
    and not exists (select 1 from leads l where l.contact_id=c.id);

notify pgrst, 'reload schema';
