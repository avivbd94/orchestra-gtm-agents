"""agents_registry.py — THE single source of the automation team (Agent Management).

Python automations and the UI both consume this. The UI's copy
(orchestra-crm-ui/lib/agents.ts) is GENERATED from here — after any change run:

    ./.venv/bin/python scripts/gen_agents_ts.py

Add an automation to an agent when you build it; an automation that isn't
listed here doesn't exist as far as governance goes.

Field notes:
  perm_keys    -> keys in app_config['permissions'] gating this agent (real
                  kill-switches the scripts check via crm/permissions.py).
  hb_prefixes  -> app_config hb_<job> heartbeat keys proving it ran.
  prompt_wired -> the agent folds agent_directives into its LLM prompts, so a
                  directive changes actual output (cadence_runner /
                  mine_how_we_met / learn_from_crm).
  scripts      -> the actual files that implement it (for the brain docs).
  avatar       -> persona portrait in orchestra-crm-ui/public/agents/<slug>.svg
                  (DiceBear notionists, downloaded locally - no runtime dependency).
"""

AGENTS = [
    {
        "slug": "scott", "name": "Scott", "face": "🔭", "avatar": "/agents/scott.svg",
        "role": "The Scout - finds new leads",
        "detail": "Watches every inbound channel for people who could become business: classifies person-to-person business emails into leads, catches website visitors (RB2B), and creates contacts for new WhatsApp conversations so nobody gets lost.",
        "workflows": [
            {"name": "email_to_lead", "what": "Inbound business emails -> auto-classified leads", "when": "nightly"},
            {"name": "rb2b webhook", "what": "Website visitor identified -> signal company", "when": "real-time"},
            {"name": "auto_create_contacts", "what": "New WhatsApp conversation -> CRM contact", "when": "real-time"},
        ],
        "integrations": ["Gmail", "RB2B", "WhatsApp (Green API)"],
        "perm_keys": ["auto_create_contacts"], "hb_prefixes": [], "prompt_wired": False,
        "scripts": ["scripts/email_to_lead.py"],
        "links": [],
    },
    {
        "slug": "gali", "name": "Gali", "face": "📥", "avatar": "/agents/gali.svg",
        "role": "The Gatherer - syncs every channel in",
        "detail": "Pulls all conversations into the CRM: Gmail every 5 minutes, WhatsApp in real-time (webhook) plus a 6-hourly DM sweep, Fathom meeting transcripts, LinkedIn imports and the Google Calendar mirror. Everything the 360 shows, Gali brought in.",
        "workflows": [
            {"name": "gmail sync", "what": "Email threads -> interactions", "when": "every 5 min"},
            {"name": "whatsapp webhook + sync_whatsapp_daily", "what": "WhatsApp DMs -> contact timelines", "when": "real-time + 6h"},
            {"name": "fathom routing", "what": "Meeting transcripts -> the right contact", "when": "per meeting"},
            {"name": "calendar_to_crm", "what": "Google Calendar -> calendar_events + meeting-booked flips", "when": "every 20 min"},
            {"name": "linkedin_inbox_sync", "what": "LinkedIn DMs via own-session voyager API -> interactions", "when": "07:40 + 15:40"},
        ],
        "integrations": ["Gmail", "WhatsApp (Green API)", "Fathom", "Google Calendar", "LinkedIn"],
        "perm_keys": [], "hb_prefixes": ["hb_gmail", "hb_whatsapp", "hb_fathom", "hb_calendar", "hb_linkedin-inbox"], "prompt_wired": False,
        "scripts": ["scripts/gmail_to_crm.py", "scripts/sync_whatsapp_daily.py", "scripts/fathom_autosync.sh", "scripts/calendar_to_crm.py", "scripts/linkedin_inbox_sync.py"],
        "links": [],
    },
    {
        "slug": "enzo", "name": "Enzo", "face": "🧬", "avatar": "/agents/enzo.svg",
        "role": "The Enricher - fills in who people are",
        "detail": "Mines names, emails, companies, titles and origin stories out of real conversation history - never invents. Fills the enrichment queue, links people to accounts, and writes the \"how we met\" line (who introduced you, on which channel).",
        "workflows": [
            {"name": "deep_enrich", "what": "Conversation history -> name/title/company/about", "when": "nightly + queue"},
            {"name": "apollo_enrich", "what": "Apollo fills missing emails on pipeline leads (fill-only, evidence-stamped)", "when": "nightly + on demand"},
            {"name": "mine_emails / mine_companies", "what": "Emails + employers mined from chat bodies", "when": "nightly"},
            {"name": "link_companies", "what": "Contacts <-> accounts by domain + enrichment", "when": "nightly"},
            {"name": "mine_how_we_met", "what": "Earliest messages -> origin story incl. introducer", "when": "nightly"},
        ],
        "integrations": ["Apollo", "Wiza", "conversation history"],
        "perm_keys": ["auto_enrich", "deep_enrich"], "hb_prefixes": [], "prompt_wired": True,
        "scripts": ["scripts/deep_enrich.py", "scripts/apollo_enrich.py", "scripts/mine_emails.py", "scripts/mine_companies.py", "scripts/link_companies.py", "scripts/mine_how_we_met.py"],
        "links": [],
    },
    {
        "slug": "dana", "name": "Dana", "face": "🧹", "avatar": "/agents/dana.svg",
        "role": "The Janitor - keeps the data clean",
        "detail": "Hunts duplicates (contacts, companies, chat lines), quarantines junk names, and keeps a review queue for anything not certain enough to merge alone. High-confidence merges only - everything else waits for you.",
        "workflows": [
            {"name": "dedup_contacts / dedup_fuzzy / dedup_cross_channel", "what": "Duplicate people merged (hard keys auto, fuzzy judged)", "when": "nightly, after backup"},
            {"name": "detect_duplicates + auto_merge_duplicates", "what": "Duplicate companies -> /duplicates queue + high-confidence auto-merge", "when": "nightly"},
            {"name": "dedup_interactions", "what": "Double-imported chat lines removed (snapshot first)", "when": "nightly"},
            {"name": "hygiene_scan", "what": "Data-health issues -> /health", "when": "nightly"},
            {"name": "normalize_names", "what": "Badly-cased Latin names recased ('natalie wilson' -> 'Natalie Wilson'); never pushed to the phone", "when": "nightly"},
            {"name": "merge_contact_pair", "what": "On-demand 'dedupe X with Y' - resolves both, dry-run plan, merges via crm/merge.py", "when": "on demand"},
            {"name": "phonebook_sync", "what": "Two-way name sync CRM ⇄ iPhone: phone edits always pulled; CRM edits pushed only when Aviv made them (UI flag)", "when": "every 10 min"},
            {"name": "resurrect_active_contacts", "what": "Archived people with live conversations come back to the boards", "when": "nightly"},
        ],
        "integrations": ["Mac Contacts (iCloud -> iPhone -> WhatsApp)"],
        "perm_keys": ["auto_merge_duplicates", "phonebook_sync"], "hb_prefixes": ["hb_phonebook-sync"], "prompt_wired": False,
        "scripts": ["scripts/dedup_contacts.py", "scripts/dedup_fuzzy.py", "scripts/dedup_cross_channel.py", "scripts/detect_duplicates.py", "scripts/auto_merge_duplicates.py", "scripts/dedup_interactions.py", "scripts/hygiene_scan.py", "scripts/merge_contact_pair.py", "scripts/phonebook_sync.py", "scripts/resurrect_active_contacts.py"],
        "links": [],
    },
    {
        "slug": "sivan", "name": "Sivan", "face": "📊", "avatar": "/agents/sivan.svg",
        "role": "The Scorer - decides who matters now",
        "detail": "Recomputes lead statuses, relationship/closeness scores, RevOps lifecycle and deal fields from the actual conversations, and keeps ONE lead per account with everyone else as buying-center contacts.",
        "workflows": [
            {"name": "compute_leads", "what": "Lead statuses + scores from interactions", "when": "nightly"},
            {"name": "relationship_score", "what": "Closeness/warmth from frequency + recency", "when": "nightly"},
            {"name": "classify_revops + deal_enrich", "what": "Lifecycle, qualification, deal fields", "when": "nightly"},
            {"name": "consolidate_account_leads", "what": "One lead per account (buying-center for the rest)", "when": "nightly"},
        ],
        "integrations": [],
        "perm_keys": ["auto_consolidate_accounts", "deal_enrich"], "hb_prefixes": [], "prompt_wired": False,
        "scripts": ["scripts/compute_leads.py", "scripts/relationship_score.sql", "scripts/classify_revops.py", "scripts/deal_enrich.py", "scripts/consolidate_account_leads.py"],
        "links": [],
    },
    {
        "slug": "omri", "name": "Omri", "face": "🚀", "avatar": "/agents/omri.svg",
        "role": "The Outreacher - runs your sequences",
        "detail": "The cadence engine: works every enrollment step by step - emails auto-send inside caps and work hours, LinkedIn messages are drafted for you to send, replies stop the sequence, three silent touches auto-unqualify. Never messages anyone outside the rules.",
        "workflows": [
            {"name": "cadence_runner", "what": "Sequence steps: email auto-send, LinkedIn -> drafts queue", "when": "every 30 min, work hours"},
            {"name": "reply-stop", "what": "A reply pauses the enrollment instantly", "when": "each run"},
            {"name": "3-touch auto-unqualify", "what": "Full sequence + silence -> Unqualified", "when": "each run"},
        ],
        "integrations": ["Gmail (send)", "LinkedIn (drafts only)"],
        "perm_keys": ["send_outbound"], "hb_prefixes": ["hb_cadence"], "prompt_wired": True,
        "scripts": ["scripts/cadence_runner.py"],
        "links": [{"label": "Cadence", "href": "/cadence"}, {"label": "LinkedIn drafts", "href": "/sequence"}],
    },
    {
        "slug": "sari", "name": "Sari", "face": "📅", "avatar": "/agents/sari.svg",
        "role": "The Secretary - calendar, tasks & prep",
        "detail": "Keeps follow-ups honest: two-way sync between every next-step (leads incl. Nurture + open deals), Google Tasks and the task board (no calendar invites - tasks only); auto-briefs you before external meetings (prep-me), sends the 08:30 reminders, and drafts every email Aviv explicitly promised to send ('אשלח לך במייל') before the promised window - draft only, never sent.",
        "workflows": [
            {"name": "followups_calendar_sync", "what": "ALL next-steps (leads+deals) ⇄ Google Tasks ⇄ /tasks (two-way, no calendar events)", "when": "every 20 min"},
            {"name": "deal_tasks", "what": "Deal next-step -> task (the only auto task source)", "when": "nightly"},
            {"name": "meeting-prep-watch", "what": "Auto prep-brief for upcoming external meetings", "when": "07:00 + 14:00"},
            {"name": "promise_followup_draft", "what": "Explicit 'I'll send you X' promises in outbound chat -> the promised email drafted in Gmail before the window (never sent)", "when": "07:15 + 15:15"},
            {"name": "morning reminders", "what": "Due follow-ups -> Mac + WhatsApp-to-self", "when": "08:30"},
        ],
        "integrations": ["Google Calendar", "Google Tasks", "WhatsApp (to self)", "Google Drive"],
        "perm_keys": ["auto_followup_tasks", "promise_followup_drafts"], "hb_prefixes": ["hb_followups", "hb_nightly", "hb_promise-followup"], "prompt_wired": True,
        "scripts": ["scripts/followups_calendar_sync.py", "scripts/deal_tasks.py", "scripts/promise_followup_draft.py"],
        "links": [],
    },
    {
        "slug": "mika", "name": "Mika", "face": "📣", "avatar": "/agents/mika.svg",
        "role": "The Marketer - content & campaigns",
        "detail": "Runs the marketing side: campaigns with approve-before-send, the AI newsletter builder grounded in your Fathom calls, per-prospect minisites, and the LinkedIn content studio (voice note -> posts in your voice). Draft-first everywhere.",
        "workflows": [
            {"name": "campaigns", "what": "Newsletter/campaign -> approve -> Gmail send", "when": "on approve"},
            {"name": "minisites", "what": "Per-prospect one-pagers -> orchestra-minisites", "when": "on publish"},
            {"name": "linkedin studio", "what": "Voice note -> HE/EN posts + carousel + video script", "when": "on demand"},
        ],
        "integrations": ["Gmail (send)", "Vercel", "LinkedIn (manual post)"],
        "perm_keys": [], "hb_prefixes": [], "prompt_wired": False,
        "scripts": ["~/orchestra-outreach/marketing/"],
        "links": [{"label": "Marketing", "href": "/marketing"}],
    },
    {
        "slug": "riki", "name": "Riki", "face": "🛡️", "avatar": "/agents/riki.svg",
        "role": "The Watchdog - guards the state machine",
        "detail": "Checks the 10 pipeline invariants nightly (a lead can never contradict its contact), verifies every dashboard number matches the board it links to, watches heartbeats for silent deaths, and reports drift - never silently fixes.",
        "workflows": [
            {"name": "qa_smoke", "what": "Background end-to-end feature smoke test: drives the LIVE app (login, 360 Copilot performs a real action, draft links resolve) and alerts if a feature breaks", "when": "every 3h"},
            {"name": "qa_artifacts", "what": "Verify generated artifacts the way a human would - drafts have a real To and Subject, stored links resolve", "when": "nightly"},
            {"name": "qa_deep_flows", "what": "User-resolution QA: cross-system seams (dead-row leaks, dedup escapes, stub bodies, stale pipes)", "when": "nightly"},
            {"name": "reconcile_sources", "what": "Source-of-truth reconciler: Fathom/Calendar/channels vs CRM - flags MISSING/THIN content", "when": "nightly"},
            {"name": "reconcile_state", "what": "10 invariants (I1-I10) on the state machine", "when": "nightly"},
            {"name": "qa_ui_consistency", "what": "Every dashboard number == its board", "when": "nightly"},
            {"name": "heartbeats + nightly_report", "what": "Silent-death detection -> WhatsApp OK/FAILED + in-app 🚨 banner", "when": "nightly"},
        ],
        "integrations": ["WhatsApp (to self)"],
        "perm_keys": [], "hb_prefixes": ["hb_nightly"], "prompt_wired": False,
        "scripts": ["scripts/reconcile_state.py", "scripts/qa_ui_consistency.py", "scripts/qa_deep_flows.py", "scripts/reconcile_sources.py", "scripts/nightly_report.py", "crm/heartbeat.py"],
        "links": [{"label": "Health", "href": "/health"}, {"label": "Activity", "href": "/activity"}],
    },
    {
        "slug": "noa", "name": "Noa", "face": "📖", "avatar": "/agents/noa.svg",
        "role": "The Learner - the CRM learning from you",
        "detail": "Studies every manual action you take (deleted tasks = noise, completed = value), tunes the generators' rules within safe bounds, and keeps a Hebrew journal of what it learned. Your actions are the spec.",
        "workflows": [
            {"name": "learn_from_crm", "what": "Your actions -> learned_rules.json + Hebrew journal", "when": "nightly"},
            {"name": "load_llm_usage", "what": "Token telemetry -> /usage", "when": "nightly"},
        ],
        "integrations": [],
        "perm_keys": [], "hb_prefixes": [], "prompt_wired": True,
        "scripts": ["scripts/learn_from_crm.py", "scripts/load_llm_usage.py"],
        "links": [{"label": "Rule Book", "href": "/rules"}, {"label": "Usage", "href": "/usage"}],
    },
    {
        "slug": "lior", "name": "Lior", "face": "📚", "avatar": "/agents/lior.svg",
        "role": "The Librarian - learns HOW you work",
        "detail": "Every hour, studies the NEW things Aviv actually did - a follow-up email he sent, a deal he reverted, a lead he tagged, a draft he edited, a correction he typed to Claude - and distils each into one minimal, reversible lesson in a living knowledge doc (docs/learned/*) that the draft generators and Copilot read back as grounding. Where Noa tunes numbers, Lior learns the soft knowledge: voice, qualification, habits. Each lesson is its own 'learn:' git commit (one revert undoes it) and a Hebrew journal line. It NEVER writes the agent_directives table - it proposes directives in AVIV_DIRECTIVES.md and Aviv promotes them.",
        "workflows": [
            {"name": "learn_live", "what": "New Aviv actions -> a minimal lesson in docs/learned/* + FOLLOWUP_EMAIL_FORMAT.md (own 'learn:' commit) + Hebrew journal", "when": "hourly"},
            {"name": "7 extractors", "what": "qualification / follow-up voice / calendar habits / tagging / draft edits / corrections-to-Claude / catch-all", "when": "each run"},
        ],
        "integrations": ["Gmail history", "Google Calendar", "Claude Code + Copilot transcripts", "git"],
        "perm_keys": ["learn_docs"], "hb_prefixes": ["hb_learn-live"], "prompt_wired": True,
        "scripts": ["scripts/learn_live.py"],
        "links": [{"label": "Journal", "href": "/activity"}, {"label": "Rule Book", "href": "/rules"}],
    },
    {
        "slug": "rafael", "name": "Rafael", "face": "🩺", "avatar": "/agents/rafael.svg",
        "role": "The Medic - heals the system when it alerts",
        "detail": "When a NEW alert or drift row appears, Rafael doesn't wait for Aviv: it prompts Claude ($0) with the problem plus the RUNBOOK/CRM_HANDOVER and the job's log tail, and for a FIXED allowlist of safe idempotent actions - restart a stale job, re-run an idempotent sync, refresh a token, reconcile_state --fix, dedup behind the backup gate, close recovered alerts - it acts. Anything outside the allowlist (code edits, deletions, config) it only diagnoses and sends to WhatsApp, draft-first. Max 3 attempts per alert, then it stops and says so. Where Riki reports drift and never fixes, Rafael fixes only what's on the list.",
        "workflows": [
            {"name": "self_heal", "what": "New alert -> allowlisted auto-fix, else diagnosis to WhatsApp; logged source=self_heal", "when": "every 10 min"},
            {"name": "attempt cap", "what": "Max 3 tries per alert then hands off to Aviv (no retry storms)", "when": "each run"},
        ],
        "integrations": ["launchd", "WhatsApp (to self)"],
        "perm_keys": ["self_heal"], "hb_prefixes": ["hb_self-heal"], "prompt_wired": False,
        "scripts": ["scripts/self_heal.py"],
        "links": [{"label": "Health", "href": "/health"}, {"label": "Activity", "href": "/activity"}],
    },
]

AGENT_BY_SLUG = {a["slug"]: a for a in AGENTS}
