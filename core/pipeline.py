"""pipeline.py — THE one writer of lead lifecycle (Phase 1 of the master plan).

Every creation/advance of a lead goes through here. The gatekeeper enforces, in
one place, the rules every generator used to half-implement:

  • vetoes are absolute: `not_a_lead`, `account_role='buying-center'`
  • one ACTIVE lead per account (extra people = buying-center contacts, not leads)
  • advance-only by default: an automation never moves a lead backwards
  • contacts.status / lifecycle_stage / pool / 'lead' tag are NEVER written here —
    they are DB-trigger projections of leads.status (migration 0030)

Scripts call:
    from core.pipeline import ensure_lead
    ensure_lead(contact_id, status="Replied", source="email_to_lead")
Returns (lead_id | None, outcome) where outcome ∈
    created | advanced | unchanged | vetoed | account_has_lead | archived
"""
from __future__ import annotations

import core.db as db

# Funnel order for advance-only comparisons (higher = further along).
from core.vocab import LEAD_STAGES as STAGE_ORDER

_RANK = {s.lower(): i for i, s in enumerate(STAGE_ORDER)}
_RANK["qualified"] = _RANK["converted"] - 0.5   # Qualified sits just below Converted
_RANK["unqualified"] = -1                        # never an automatic "advance"
_RANK["nurture"] = _RANK["contacted"] + 0.5      # holding state, not ahead of Replied


def _rank(status: str | None) -> int:
    return _RANK.get((status or "").strip().lower(), 0)


def _q(s: str) -> str:
    return str(s).replace("'", "''")


def ensure_lead(contact_id: str, *, status: str = "New Lead", source: str | None = None,
                outbound_type: str | None = None, manual: bool = False,
                allow_downgrade: bool = False) -> tuple[str | None, str]:
    """Create the contact's lead, or advance the existing one. Enforces every
    gate. `manual=True` marks status_manual (a human pinned it)."""
    row = db.run_sql(
        "select coalesce(archived,false), custom_fields->>'not_a_lead', "
        "custom_fields->>'account_role', company_id "
        f"from contacts where id='{_q(contact_id)}'")
    if not row:
        return None, "vetoed"
    archived, not_a_lead, account_role, company_id = row[0]
    if archived:
        return None, "archived"
    if not_a_lead == "true":
        return None, "vetoed"
    if account_role == "buying-center":
        return None, "vetoed"

    existing = db.run_sql(
        f"select id, status from leads where contact_id='{_q(contact_id)}' limit 1")
    if existing:
        lead_id, cur = existing[0]
        if _rank(status) > _rank(cur) or (allow_downgrade and status != cur):
            db.run_sql(
                f"update leads set status='{_q(status)}', "
                f"status_manual={'true' if manual else 'false'}, updated_at=now() "
                f"where id='{lead_id}'")
            return str(lead_id), "advanced"
        return str(lead_id), "unchanged"

    # One ACTIVE lead per account: if the company already has one, this person is
    # buying-center, not a second pipeline entry.
    if company_id:
        other = db.run_sql(
            f"""select 1 from leads l join contacts c on c.id=l.contact_id
                where l.company_id='{company_id}'
                  and lower(coalesce(l.status,'')) not in ('converted','unqualified')
                  and coalesce(c.archived,false)=false limit 1""")
        if other:
            db.run_sql(
                "update contacts set custom_fields = coalesce(custom_fields,'{}'::jsonb) || "
                f"'{{\"account_role\": \"buying-center\"}}'::jsonb where id='{_q(contact_id)}'")
            return None, "account_has_lead"

    ob = f"'{_q(outbound_type)}'" if outbound_type else "null"
    co = f"'{company_id}'" if company_id else "null"
    lead_id = db.run_sql(
        "insert into leads (contact_id, company_id, status, status_manual, outbound_type) "
        f"values ('{_q(contact_id)}', {co}, '{_q(status)}', {'true' if manual else 'false'}, {ob}) "
        "returning id")[0][0]
    if source:
        try:
            from core.activity import log
            log("create", entity_type="lead", entity_id=str(lead_id), source=source,
                summary=f"lead created at '{status}'")
        except Exception:
            pass
    return str(lead_id), "created"


def remove_lead(contact_id: str, *, veto: bool = True, source: str | None = None) -> bool:
    """'Not a lead': drop the lead row; optionally set the durable veto. The
    projections demote contacts.status automatically."""
    db.run_sql(f"delete from leads where contact_id='{_q(contact_id)}'")
    if veto:
        db.run_sql(
            "update contacts set custom_fields = coalesce(custom_fields,'{}'::jsonb) || "
            f"'{{\"not_a_lead\": true}}'::jsonb, updated_at=now() where id='{_q(contact_id)}'")
    if source:
        try:
            from core.activity import log
            log("demote", entity_type="contact", entity_id=contact_id, source=source,
                summary="removed from pipeline" + (" (vetoed)" if veto else ""))
        except Exception:
            pass
    return True
