"""heartbeat.py — automation liveness (defect class D4: silent death).

Every launchd job calls beat('<job>') when it runs; check_stale() (run by the
10-minute watchdog) compares each heartbeat against the job's expected interval
and pings Aviv ONCE per outage when a job goes silent — the class of failure
where the WhatsApp label sync was dead for weeks and nobody knew.

Stamps live in app_config as hb_<job> so the /health Automations panel can read
them with plain PostgREST.
"""
from __future__ import annotations
from datetime import datetime, timezone

import core.db as db

# job -> expected interval in minutes (2x = stale). nightly is a daily calendar job.
EXPECTED_MIN: dict[str, int] = {
    "nightly": 60 * 26,        # daily at 04:30; alert if >26h since last run
    "gmail-sync": 5,
    "whatsapp-daily": 360,
    "ai-jobs": 5,
    "calendar-sync": 15,
    # fathom-sync fires every 20 min but ONE run (ingest + drafting + deal
    # mining, all LLM) routinely takes 40-60 min, so a 20-min expectation
    # flapped 'down' on every long run — 7 false WhatsApp alarms in one day
    # (2026-07-19) and Rafael kicked a job that was mid-run. 90 = stale at 3h,
    # which only a genuinely stuck chain reaches.
    "fathom-sync": 90,
    "cadence": 30,
    "followups": 360,
    # QA layer (2026-07-18): tracked so (a) a QA job that stops running goes
    # visibly stale (they beat only after completing - O13) and (b) their
    # 'automation down: <job>' alerts auto-close via resolve_recovered once
    # the job is healthy again (before this, a qa-smoke alert lived forever).
    "qa-smoke": 200,           # every 3h
    "qa-artifacts": 60 * 26,   # nightly chain
    "qa-deep": 60 * 26,        # nightly chain
    "reconcile-sources": 60 * 26,
    "phonebook-sync": 30,      # every 10 min
    # Learning + self-healing layer (2026-07-18). Both beat only AFTER a real
    # successful run (truthful heartbeat) — a beat means the pass completed, not
    # merely that launchd fired the job.
    # promise watcher (2026-07-19): 07:15 + 15:15 - beats after every completed
    # run (incl. permission-off no-op); stale only past a full missed day.
    "promise-followup": 60 * 26,
    "learn-live": 90,          # hourly qualitative learner (Lior); stale at 3h
    "self-heal": 30,           # alert responder (Rafael), every 10 min; stale at 1h
    "linkedin-connect": 60 * 48,  # weekday bare-connect campaign; generous (weekend gap)
}


def beat(job: str) -> None:
    """Stamp the job's heartbeat. Never raises into the job."""
    try:
        db.run_sql(
            "insert into app_config (key, value, updated_at) values "
            f"('hb_{job}', '{datetime.now(timezone.utc).isoformat()}', now()) "
            "on conflict (key) do update set value=excluded.value, updated_at=now()")
    except Exception:
        pass


def status() -> list[dict]:
    """Every known job with its last beat + staleness (for /health + the checker)."""
    rows = dict(db.run_sql("select key, value from app_config where key like 'hb_%'"))
    now = datetime.now(timezone.utc)
    out = []
    for job, exp_min in EXPECTED_MIN.items():
        raw = rows.get(f"hb_{job}")
        last = None
        age_min = None
        if raw:
            try:
                last = datetime.fromisoformat(raw)
                age_min = (now - last).total_seconds() / 60
            except ValueError:
                pass
        out.append({
            "job": job, "expected_min": exp_min,
            "last": raw, "age_min": round(age_min, 1) if age_min is not None else None,
            # never-beaten jobs aren't stale (freshly deployed) - they show as 'no data'
            "stale": bool(age_min is not None and age_min > 2 * exp_min),
        })
    return out


def resolve_recovered() -> int:
    """Auto-close alerts whose job(s) are healthy again. An irrelevant alert must
    disappear on its own (Aviv 2026-07-18) - not linger in the banner or pile up.
    Matches both formats: 'automation stale: a, b' and 'automation down: job.'.
    Sets action='alert_resolved' so it drops out of the banner and stops
    accumulating. Returns how many it closed."""
    import re
    healthy = {s["job"] for s in status() if not s["stale"]}
    rows = db.run_sql("select id, coalesce(context->>'summary','') from crm_actions where action='alert'")
    closed = 0
    for aid, summ in rows:
        m = re.search(r"automation (?:stale|down):\s*([a-z0-9 ,_-]+)", summ, re.I)
        if not m:
            continue
        jobs = [j.strip() for j in m.group(1).replace(".", "").split(",") if j.strip()]
        jobs = [j for j in jobs if j]  # e.g. ['qa-smoke'] or ['nightly','gmail-sync',...]
        if not jobs:
            continue
        still = [j for j in jobs if j not in healthy]
        if not still:
            db.run_sql(f"update crm_actions set action='alert_resolved' where id='{aid}'")
            closed += 1
        elif len(still) < len(jobs):
            # partial recovery: shrink the banner to ONLY the still-stale jobs so
            # recovered ones stop being blamed (an outage alert bundles many jobs
            # and used to linger whole until the LAST one recovered).
            new_summ = summ[:m.start(1)] + ", ".join(still) + summ[m.end(1):]
            new_summ = new_summ.replace("'", "''")
            db.run_sql("update crm_actions set context = jsonb_set(context, '{summary}', "
                       f"to_jsonb('{new_summ}'::text)) where id='{aid}'")
    return closed


GRACE_MIN = 30   # Rafael's head start before a staleness reaches WhatsApp


def check_stale(notify: bool = True) -> list[str]:
    """Alert once per outage: remembers the last alerted beat per job so a dead
    job pings when it dies, not every 10 minutes forever. Also auto-closes any
    alert whose job has since recovered, so stale/irrelevant alerts self-clear.

    GRACE WINDOW (Aviv 2026-07-19: "too many WhatsApp alerts"): a fresh
    staleness opens the banner ALERT immediately, but the WhatsApp ping waits
    GRACE_MIN minutes — self_heal (Rafael, every 10 min) usually restarts or
    resolves it first. Only a staleness that SURVIVES the grace reaches the
    phone. hb_pending_<job> tracks the wait; recovery clears it."""
    resolve_recovered()
    all_status = status()
    stale = [s for s in all_status if s["stale"]]
    # clear pending markers for jobs that recovered before their grace expired
    pend = dict(db.run_sql("select key, value from app_config where key like 'hb_pending_%'"))
    stale_jobs = {s["job"] for s in stale}
    for key in pend:
        if key.replace("hb_pending_", "") not in stale_jobs:
            db.run_sql(f"delete from app_config where key='{key}'")
    if not stale:
        return []
    alerted = dict(db.run_sql("select key, value from app_config where key like 'hb_alerted_%'"))
    now = datetime.now(timezone.utc)
    fresh = []
    for s in stale:
        marker = alerted.get(f"hb_alerted_{s['job']}")
        if marker == s["last"]:
            continue  # already alerted for this outage
        pkey = f"hb_pending_{s['job']}"
        pval = pend.get(pkey)
        if not pval:
            # first sighting: open the banner alert, start the grace clock,
            # no WhatsApp yet — Rafael gets first crack at it.
            db.run_sql(
                "insert into app_config (key, value, updated_at) values "
                f"('{pkey}', '{now.isoformat()}', now()) "
                "on conflict (key) do update set value=excluded.value, updated_at=now()")
            try:
                from core.activity import log
                log("alert", source="heartbeat", summary=f"automation stale: {s['job']}")
            except Exception:
                pass
            continue
        try:
            pending_since = datetime.fromisoformat(pval)
        except Exception:
            pending_since = now
        if (now - pending_since).total_seconds() < GRACE_MIN * 60:
            continue  # still inside Rafael's grace window
        fresh.append(s["job"])
        db.run_sql(
            "insert into app_config (key, value, updated_at) values "
            f"('hb_alerted_{s['job']}', '{s['last'] or 'never'}', now()) "
            "on conflict (key) do update set value=excluded.value, updated_at=now()")
        db.run_sql(f"delete from app_config where key='{pkey}'")
    if fresh and notify:
        try:
            from scripts.notify_self import notify_self
            jobs = ", ".join(fresh)
            notify_self(f"🔴 CRM automation down: {jobs} — stale for over {GRACE_MIN} min and self-heal couldn't fix it. Check /health.",
                        mac_subtitle="CRM automations")
        except Exception:
            pass
        # (the banner alert row was already logged at first sighting)
    return fresh
