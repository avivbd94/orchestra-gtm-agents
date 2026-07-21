#!/usr/bin/env python3
"""reconcile_state.py — the state machine's watchdog (Phase 1 of the master plan).

Checks every projection invariant and REPORTS drift - it never silently fixes,
because drift means some writer escaped the gatekeeper and must be found and
ported, not papered over. One activity entry + one WhatsApp line when dirty;
silent when clean.

Invariants checked:
  I1  contact has an active lead        -> contacts.status = 'lead' (or customer)
  I2  contact has a Closed-Won deal     -> contacts.status = 'customer'
  I2b contacts.status = 'customer'      -> a Closed-Won deal exists (0048)
  I3  contacts.status = 'lead'          -> active lead OR open converted deal
  I4  leads.pool                        == pool_of_status(leads.status)
  I5  leads.lifecycle_stage             == lifecycle_of(leads.status)
  I6  'lead' tag                        <-> active lead exists
  I7  one ACTIVE lead per company
  I8  vetoed / buying-center contacts have no lead row
  I9  converted deal                     -> lead status stays 'Converted'
  I10 lead.company_id                    == its contact's company_id

  ./.venv/bin/python scripts/reconcile_state.py           # report only
  ./.venv/bin/python scripts/reconcile_state.py --fix     # apply I1-I6 repairs too
"""
from __future__ import annotations
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import core.db as db
from core.vocab import PROSPECT_STATUSES

_P = ",".join(f"'{s}'" for s in PROSPECT_STATUSES)
# 0048: lifecycle is deal-aware — Customer only when the linked deal is Closed
# Won; an open (or unresolvable) deal is 'Opportunity'; a lost deal drops out.
# The I5 query must LEFT JOIN opportunities o2 on o2.id=l.converted_opportunity_id.
_LIFE = """case
    when o2.stage = 'Closed Won'                                          then 'Customer'
    when o2.stage = 'Closed Lost'                                         then null
    when l.converted_opportunity_id is not null or l.status = 'Converted' then 'Opportunity'
    when l.status = 'Meeting booked'                                      then 'Meeting'
    when l.status = 'Qualified'                                           then 'Qualified'
    when l.status in ('Replied','Waiting for a reply')                    then 'Engaged'
    when l.status = 'Nurture'                                             then 'Nurture'
    when l.status = 'Unqualified'                                         then null
    else 'Lead' end"""

CHECKS: list[tuple[str, str]] = [
    ("I1 active lead but contact not lead/customer",
     """select count(*) from contacts c
        where exists (select 1 from leads l where l.contact_id=c.id
                      and lower(coalesce(l.status,'')) not in ('converted','unqualified'))
          and coalesce(c.archived,false)=false
          and c.status not in ('lead','customer')"""),
    ("I2 won deal but contact not customer",
     """select count(*) from contacts c
        where (exists (select 1 from opportunities o where o.contact_id=c.id and o.stage='Closed Won')
               or exists (select 1 from leads l join opportunities o on o.id=l.converted_opportunity_id
                          where l.contact_id=c.id and o.stage='Closed Won'))
          and c.status is distinct from 'customer'"""),
    ("I2b customer without a won deal",
     """select count(*) from contacts c
        where c.status='customer'
          and not exists (select 1 from opportunities o where o.contact_id=c.id and o.stage='Closed Won')
          and not exists (select 1 from leads l join opportunities o on o.id=l.converted_opportunity_id
                          where l.contact_id=c.id and o.stage='Closed Won')"""),
    ("I3 status=lead but no pipeline (active lead or open deal)",
     """select count(*) from contacts c
        where c.status='lead'
          and not exists (select 1 from leads l where l.contact_id=c.id
                          and lower(coalesce(l.status,'')) not in ('converted','unqualified'))
          and not exists (select 1 from leads l join opportunities o on o.id=l.converted_opportunity_id
                          where l.contact_id=c.id and o.stage not in ('Closed Won','Closed Lost'))"""),
    ("I4 pool drift",
     f"""select count(*) from leads
         where pool is distinct from (case when status in ({_P}) then 'prospect' else 'suspect' end)"""),
    ("I5 lifecycle drift",
     f"""select count(*) from leads l
         left join opportunities o2 on o2.id = l.converted_opportunity_id
         where l.lifecycle_stage is distinct from ({_LIFE})"""),
    ("I6 'lead' tag drift",
     """select count(*) from contacts c
        where (coalesce(c.tags,'{}') @> array['lead']) is distinct from
              exists (select 1 from leads l where l.contact_id=c.id
                      and lower(coalesce(l.status,'')) not in ('converted','unqualified'))"""),
    ("I7 company with >1 active lead",
     """select count(*) from (
          select l.company_id from leads l join contacts c on c.id=l.contact_id
          where l.company_id is not null
            and lower(coalesce(l.status,'')) not in ('converted','unqualified')
            and coalesce(c.archived,false)=false
          group by l.company_id having count(*)>1) x"""),
    ("I9 converted deal but lead status regressed",
     """select count(*) from leads
        where converted_opportunity_id is not null and status is distinct from 'Converted'"""),
    ("I10 lead company differs from contact company",
     """select count(*) from leads l join contacts c on c.id=l.contact_id
        where c.company_id is not null and l.company_id is distinct from c.company_id"""),
    ("I8 vetoed/buying-center contact has a lead row",
     """select count(*) from contacts c join leads l on l.contact_id=c.id
        where (c.custom_fields->>'not_a_lead'='true'
               or c.custom_fields->>'account_role'='buying-center')
          and lower(coalesce(l.status,'')) not in ('converted','unqualified')"""),
]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true",
                    help="repair I1-I6 (projection re-derivation); I7/I8 always need a human/consolidator")
    args = ap.parse_args(argv)

    drift = {}
    for name, sql in CHECKS:
        n = db.run_sql(sql)[0][0]
        if n:
            drift[name] = n
        print(f"  {'✗' if n else '✓'} {name}: {n}")

    if not drift:
        print("reconcile: clean.")
        # A clean full pass SELF-CLEARS older drift alerts in the app's 🚨
        # banner (/api/alerts hides drift rows older than the latest clean run).
        try:
            from core.activity import log
            log("reconcile", source="reconcile_state", summary="clean")
        except Exception:
            pass
        return

    if args.fix:
        # Re-derive projections by touching every lead row (fires the triggers).
        db.run_sql("update leads set status = status")
        # I3: stale 'lead' status with no row -> contact.
        db.run_sql("""update contacts c set status='contact', updated_at=now()
                      where c.status='lead'
                        and not exists (select 1 from leads l where l.contact_id=c.id
                                        and lower(coalesce(l.status,'')) not in ('converted','unqualified'))""")
        # I6 (tag side the lead-touch can't reach): a contact whose lead row is
        # GONE (e.g. merge loser) keeps a stale 'lead' tag — strip it directly.
        db.run_sql("""update contacts c set tags = array_remove(tags,'lead'), updated_at=now()
                      where coalesce(c.tags,'{}') @> array['lead']
                        and not exists (select 1 from leads l where l.contact_id=c.id
                                        and lower(coalesce(l.status,'')) not in ('converted','unqualified'))""")
        print("fix: projections re-derived (I7/I8 left for the consolidator/human).")

    detail = "; ".join(f"{k}={v}" for k, v in drift.items())
    try:
        from core.activity import log
        log("drift", source="reconcile_state", summary=f"state drift: {detail}", **{k.split()[0]: v for k, v in drift.items()})
    except Exception:
        pass
    try:
        from scripts.notify_self import notify_self
        notify_self(f"⚠️ CRM state drift: {detail} — a writer escaped the gatekeeper (see /activity).",
                    mac_subtitle="CRM reconciler")
    except Exception:
        pass


if __name__ == "__main__":
    main()
