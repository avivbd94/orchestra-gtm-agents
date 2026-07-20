"""Permission management — what the CRM's automations are ALLOWED to do, managed
by Aviv in plain text from the Rule Book. Stored as JSON in app_config['permissions'].

Each permission has a `mode` and a human `note`. Automations call is_allowed()/mode()
before acting, so Aviv governs autonomy centrally instead of it being hard-coded in
each script. The defaults encode the standing rules (never send outbound without
approval; auto-merge only high-confidence dupes; manual tasks only).

  from core.permissions import mode, is_allowed
  if mode("auto_merge_duplicates") == "high_only": ...
  if is_allowed("auto_create_contacts"): create...
"""
from __future__ import annotations
import json

import core.db as db

# key -> {mode, note, options}. options = the modes Aviv may choose in the UI.
DEFAULTS: dict[str, dict] = {
    "auto_merge_duplicates": {
        "mode": "high_only",
        "note": "Auto-merge high-confidence duplicate companies; medium/'possible' pairs go to the review queue, never auto-merged.",
        "options": ["off", "high_only", "all"]},
    "auto_create_contacts": {
        "mode": "on",
        "note": "When you message a new person on WhatsApp (or they message you), create a CRM contact so they never get lost.",
        "options": ["off", "on"]},
    "send_outbound": {
        "mode": "ask",
        "note": "Never send email / WhatsApp / LinkedIn on Aviv's behalf without explicit approval. Automations draft only.",
        "options": ["ask", "auto"]},
    "auto_consolidate_accounts": {
        "mode": "on",
        "note": "Keep ONE lead per account; extra people at the same company become buying-center contacts, not separate leads.",
        "options": ["off", "on"]},
    "auto_enrich": {
        "mode": "on",
        "note": "Enrich contacts from their real conversation history (never attribute a third party's email/company to them).",
        "options": ["off", "on"]},
    "deep_enrich": {
        "mode": "on",
        "note": "Contact Enricher: read each contact's full history -> name/company/title/about (the nightly LLM-heavy pass). OFF = paused (Aviv 2026-07-19).",
        "options": ["off", "on"]},
    "deal_enrich": {
        "mode": "on",
        "note": "Deal Enricher: mine deal stage / next-step fields from conversations. OFF = paused (Aviv 2026-07-19).",
        "options": ["off", "on"]},
    "auto_followup_tasks": {
        "mode": "off",
        "note": "Do NOT auto-create follow-up tasks. Tasks come only from booked-deal next steps.",
        "options": ["off", "on"]},
    "promise_followup_drafts": {
        "mode": "on",
        "note": "When Aviv explicitly promises in chat to send something ('אשלח לך', 'ההצעה במייל', \"I'll send you...\"), auto-DRAFT the promised email in Gmail before the promised window. Draft only - sending stays with Aviv (send_outbound unaffected).",
        "options": ["off", "on"]},
    "learn_docs": {
        "mode": "on",
        "note": "Let the learning agent (Lior) write what it learns from your actions into docs/learned/* + the follow-up format doc (each as its own reversible 'learn:' git commit). OFF = it still reads and journals, but writes no docs. It NEVER writes the agent_directives table — you promote directives yourself.",
        "options": ["off", "on"]},
    "self_heal": {
        "mode": "on",
        "note": "Let the self-healing watcher (Rafael) act on alerts without waiting for you, but ONLY within a fixed allowlist (restart a stale job, re-run an idempotent sync, refresh a token, reconcile_state --fix, dedup behind backup, close recovered alerts). Anything else it only diagnoses and messages you. Max 3 attempts per alert. OFF = it diagnoses + messages only, never acts.",
        "options": ["off", "on"]},
}

_KEY = "permissions"


def all_permissions() -> dict[str, dict]:
    """Current permissions = defaults overlaid with saved overrides."""
    out = {k: dict(v) for k, v in DEFAULTS.items()}
    try:
        rows = db.run_sql("select value from app_config where key='%s'" % _KEY)
        if rows and rows[0][0]:
            saved = json.loads(rows[0][0])
            for k, v in saved.items():
                if k in out and isinstance(v, dict):
                    out[k].update({kk: v[kk] for kk in ("mode", "note") if kk in v})
                elif isinstance(v, dict):
                    out[k] = v
    except Exception:
        pass
    return out


def mode(key: str) -> str:
    p = all_permissions().get(key) or DEFAULTS.get(key) or {}
    return p.get("mode", "off")


def is_allowed(key: str) -> bool:
    """True for an on/auto permission. 'ask'/'off'/'high_only' are not a blanket yes."""
    return mode(key) in ("on", "auto", "all")


def set_permission(key: str, new_mode: str | None = None, note: str | None = None) -> dict:
    cur = all_permissions()
    entry = cur.get(key, {"mode": "off", "note": ""})
    if new_mode is not None:
        entry["mode"] = new_mode
    if note is not None:
        entry["note"] = note
    cur[key] = entry
    payload = json.dumps({k: {"mode": v["mode"], "note": v.get("note", "")} for k, v in cur.items()})
    db.run_sql(
        "insert into app_config (key, value, updated_at) values ('%s', %s, now()) "
        "on conflict (key) do update set value=excluded.value, updated_at=now()"
        % (_KEY, "'" + payload.replace("'", "''") + "'"))
    return entry
