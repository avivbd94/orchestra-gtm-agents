"""vocab.py — THE single source of the pipeline vocabulary (defect class D6).

Python imports these directly; the UI's copy (orchestra-crm-ui/lib/vocab.ts) is
GENERATED from here by scripts/gen_vocab.py — run it after any change:

    ./.venv/bin/python scripts/gen_vocab.py

The DB trigger (0030/leads_set_pool) keeps its own copy of PROSPECT_STATUSES in
SQL; the nightly reconciler cross-checks pool projections, so trigger drift is
caught within a day.
"""

# Full funnel order (drives boards, advance-only ranking, group ordering).
LEAD_STAGES = [
    "New Lead", "Connected", "To Contact", "Message Sent", "Contacted",
    "Replied", "Meeting booked", "Waiting for a reply", "Nurture",
    "Unqualified", "Converted",
]

# Statuses that put a lead in the PROSPECT pool (pipeline); the rest = suspect.
PROSPECT_STATUSES = [
    "Replied", "Meeting booked", "Waiting for a reply", "Qualified",
    "Nurture", "Unqualified", "Converted",
]

# Lifecycle ladder (projection of leads.status - see 0030).
LIFECYCLE_STAGES = [
    "Suspect", "Lead", "Engaged", "Nurture", "Qualified",
    "Meeting", "Opportunity", "Customer",
]

# MEDDIC buying-center roles.
BUYING_ROLES = [
    "Champion", "Decision Maker", "Economic Buyer", "Gatekeeper",
    "Influencer", "Other",
]
