"""activity.py — the audit trail every module writes to.

In production this feeds an /activity feed in the UI. Here it is the minimal
working version: one row per action into `crm_actions` (created below if
missing), falling back to stderr if the DB is unreachable. The point of the
pattern: EVERY autonomous action leaves a queryable trace with its source —
"which writer did this" is rule 5's fingerprint question, and it is only
answerable if writers sign their work.
"""
from __future__ import annotations
import json
import sys
from datetime import datetime, timezone

import core.db as db

_DDL = """create table if not exists crm_actions (
  id bigint generated always as identity primary key,
  ts timestamptz not null default now(),
  action text not null,
  source text,
  entity_type text,
  entity_id text,
  context jsonb
)"""
_ready = False


def log(action: str, *, source: str | None = None, entity_type: str | None = None,
        entity_id: str | None = None, summary: str | None = None, **extra) -> None:
    """Record one action. Never raises into the caller."""
    global _ready
    ctx = {k: v for k, v in {"summary": summary, **extra}.items() if v is not None}
    try:
        if not _ready:
            db.run_sql(_DDL)
            _ready = True
        q = lambda s: str(s).replace("'", "''")
        db.run_sql(
            "insert into crm_actions (action, source, entity_type, entity_id, context) values "
            f"('{q(action)}', "
            + (f"'{q(source)}'" if source else "null") + ", "
            + (f"'{q(entity_type)}'" if entity_type else "null") + ", "
            + (f"'{q(entity_id)}'" if entity_id else "null") + ", "
            f"'{q(json.dumps(ctx, ensure_ascii=False))}'::jsonb)")
    except Exception:
        print(f"[activity] {datetime.now(timezone.utc).isoformat()} {action} "
              f"source={source} {ctx}", file=sys.stderr)
