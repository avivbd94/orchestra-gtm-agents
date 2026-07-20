"""notify_self.py — the "reach a human" channel.

In production this sends a WhatsApp message to the operator (plus a macOS
notification). Here it is a stub that prints, because the delivery channel is
the least interesting part — wire it to whatever reaches you.

The pattern that matters is WHO calls it and when: the heartbeat checker after
a staleness survives the self-heal grace window, and the reconciler when drift
appears. Alerts fire on the failure a human must see, not on every hiccup.
"""
from __future__ import annotations
import sys


def notify_self(message: str, mac_subtitle: str | None = None) -> None:
    """Deliver `message` to the operator. Replace the body with your channel
    (WhatsApp API, Telegram bot, ntfy.sh, email) — keep the signature."""
    print(f"[notify] {message}", file=sys.stderr)
