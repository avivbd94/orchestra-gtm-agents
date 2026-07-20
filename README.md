# GTM Agents — the guardrails

The architecture behind a self-built CRM that runs 12 agents across 25 scheduled
jobs, on a Claude subscription, with a $0 API bill.

**This is not the CRM.** It is the part worth stealing: the rules that stop
agents from quietly corrupting your data, extracted as working code you can read
in an afternoon.

The agents were the easy half. These rules were the real build.

---

## The 7 rules

### 1. One writable field
`leads.status` is the only lifecycle field anything writes. Pool, stage, contact
state and the lead tag are **projections**, derived by database triggers.

An agent cannot drift your pipeline because there is nothing else to write to.

→ [`core/pipeline.py`](core/pipeline.py) · [`db/migrations/0030_state_machine.sql`](db/migrations/0030_state_machine.sql)

### 2. Agents draft. One agent sends.
Every outbound surface produces text and stops. One engine may deliver, inside
hard caps and sending hours, behind a switch you can flip off.

An agent that can email your pipeline is one bad prompt from an incident.

→ [`core/permissions.py`](core/permissions.py) (`send_outbound`)

### 3. Dry-run is the default
Every job runs read-only until you pass `--live`. Every job is idempotent. No
agent writes to production as a side effect of running.

→ [`scripts/reconcile_state.py`](scripts/reconcile_state.py)

### 4. No delete without a snapshot
Destructive steps run only after the backup succeeded, and snapshot every row
they remove. The rule is not "never let agents delete" — it is "make every
delete reversible."

### 5. Invariants, checked nightly
Ten of them. *An active lead means the contact is marked a lead. One active lead
per account. A converted deal cannot regress.*

It reports; it never silent-fixes. A silent fix hides the writer that escaped.

→ [`scripts/reconcile_state.py`](scripts/reconcile_state.py)

### 6. A named gate on every risky action
`send_outbound`, `auto_merge_duplicates`, `auto_enrich`, `learn_docs`,
`self_heal`. Each has a mode you set at runtime — no deploy, no code change —
and the job checks it before acting.

A permission an agent can ignore is a suggestion.

→ [`core/permissions.py`](core/permissions.py)

### 7. Audit the output, not the exit code
A green log means the code executed, not that the output was right. So separate
agents check the work the way a human would: open the draft, load the page,
confirm the link resolves.

Most agent failures are not errors. They are confident, well-formatted, and wrong.

→ [`core/heartbeat.py`](core/heartbeat.py)

---

## What's in here

```
core/
  pipeline.py          the gatekeeper - the only sanctioned lifecycle writer
  permissions.py       runtime gates, one source of defaults
  heartbeat.py         proof-of-life per job + staleness detection
  agents_registry.py   the registry pattern: every automation belongs to an agent
  llm.py               subscription-first model routing (never the metered API)
  vocab.py             the shared stage vocabulary
  db.py                thin Postgres access
scripts/
  reconcile_state.py   the 10-invariant nightly reconciler
db/migrations/         the trigger-based projections that make rule 1 hold
```

## Running it

```bash
python3.11 -m venv .venv && ./.venv/bin/pip install psycopg2-binary
cp secrets.example.json secrets.json     # fill in your own Postgres
./.venv/bin/python scripts/reconcile_state.py          # dry-run, reports drift
./.venv/bin/python scripts/reconcile_state.py --fix    # repairs projections
```

Every script is dry-run by default. `--live` / `--fix` is always a deliberate act.

## About `agents_registry.py`

That file is the **real production roster** — all 12 agents, their workflows,
schedules and permission keys, exactly as they run. It references scripts that
are not in this repo (channel syncs, enrichment, dedup): those are coupled to
private accounts and data. The registry ships anyway because the *pattern* is
the point — every automation belongs to a named agent, every risky action maps
to a gate, and an automation that isn't registered doesn't exist as far as
governance goes.

## What this is not

- Not a product, not a framework, not a package. Read it, take the patterns.
- No channel integrations (Gmail / WhatsApp / LinkedIn / calendar syncs). Those
  are coupled to accounts and data that cannot be published.
- No UI.
- The schema assumes Postgres with `contacts`, `leads` and `app_config` tables.
  The migrations show the shape.

## Licence

MIT.
