"""LLM backend switch — Claude subscription (`claude -p`), local Ollama, or API.

Aviv (2026-07-02): the CRM must not spend Anthropic API tokens. All local
Python AI scripts route through here.

Default backend (2026-07-05): `subscription` — shells out to `claude -p`
(Aviv's Claude Code subscription, $0, full Claude quality). If the CLI isn't
logged in it self-heals down to the LOCAL Ollama model (qwen2.5:7b-instruct,
also $0) — it never silently falls back to the paid API.

get_llm(cfg) returns an object with the same surface the scripts already use:
    client.messages.create(model=..., max_tokens=..., messages=[{role,content}])
    -> resp.content[0].text
so a script only changes ONE line: anthropic.Anthropic(...) -> get_llm(cfg).

Env:
    LLM_BACKEND  = subscription (default) | ollama | anthropic
    OLLAMA_MODEL = qwen2.5:7b-instruct (default, used when backend=ollama)
    OLLAMA_URL   = http://localhost:11434
"""
from __future__ import annotations

import atexit
import json
import os
import pathlib
import subprocess
import sys
import threading
import urllib.request
from datetime import datetime, timezone

# $ per 1M tokens (input, output) — used to estimate cost when the backend does
# not report one (the paid API SDK). The subscription CLI reports its own cost.
PRICING = {
    "claude-opus-4-8": (5.0, 25.0), "claude-opus-4-7": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0), "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0), "claude-haiku-4-5-20251001": (1.0, 5.0),
}


def _purpose() -> str:
    """Which routine made the call — the running script's name, e.g.
    'deep_enrich'. Overridable via LLM_PURPOSE (set by the UI proxy)."""
    p = os.environ.get("LLM_PURPOSE")
    if p:
        return p[:60]
    base = os.path.basename(sys.argv[0] or "") if sys.argv else ""
    if base.endswith(".py"):
        base = base[:-3]
    return base or "unknown"


def _price(model: str | None, tin: int, tout: int,
           cache_read: int = 0, cache_creation: int = 0) -> float:
    """Estimate paid-API cost (M2). Anthropic cache economics: cache READS bill at
    ~0.1x input, cache WRITES at ~1.25x input. `tin` is fresh (uncached) input."""
    for key, (pin, pout) in PRICING.items():
        if model and key in model:
            return round(tin / 1e6 * pin
                         + cache_read / 1e6 * pin * 0.1
                         + cache_creation / 1e6 * pin * 1.25
                         + tout / 1e6 * pout, 6)
    # N4: an unpriced model would silently under-report real spend — warn, don't hide.
    if model:
        print(f"[llm_usage] WARN: no price for model {model!r}; cost logged as $0",
              file=sys.stderr)
    return 0.0


# Telemetry transport: append one JSON line per call to a LOCAL file. This is
# lock-guarded, microseconds, needs no network, and can never block/stall or
# corrupt the LLM call it measures (the one property telemetry must not violate).
# scripts/load_llm_usage.py bulk-loads these lines into the llm_usage table and
# truncates the file (wired into nightly_refresh.sh / morning_followups.sh).
_USAGE_LOG = pathlib.Path(__file__).resolve().parent.parent / "logs" / "llm_usage.jsonl"
_usage_lock = threading.Lock()
_usage_dropped = 0


def record_usage(backend, model, input_tokens, output_tokens,
                 cost_usd=0.0, cache_read_tokens=0, purpose=None):
    """Append one usage record to logs/llm_usage.jsonl. Best-effort, but drops
    are COUNTED (not silently swallowed) and surfaced at process exit."""
    global _usage_dropped
    try:
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "backend": backend, "model": model or "",
            "purpose": purpose or _purpose(),
            "input_tokens": int(input_tokens or 0),
            "cache_read_tokens": int(cache_read_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "cost_usd": float(cost_usd or 0),
        }
        line = json.dumps(rec, ensure_ascii=False)
        with _usage_lock:
            _USAGE_LOG.parent.mkdir(exist_ok=True)
            with open(_USAGE_LOG, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        _usage_dropped += 1


@atexit.register
def _warn_dropped_usage():
    if _usage_dropped:
        print(f"[llm_usage] WARNING: dropped {_usage_dropped} usage record(s) "
              f"(logging failed)", file=sys.stderr)


class _Block:
    def __init__(self, text): self.text = text


class _Resp:
    def __init__(self, text): self.content = [_Block(text)]


class _RecordingAnthropic:
    """Wraps the paid anthropic.Anthropic client so every call is logged to
    llm_usage as backend='anthropic' (real API spend)."""
    def __init__(self, inner):
        self._inner = inner
        self.messages = self

    def create(self, model=None, **kw):
        resp = self._inner.messages.create(model=model, **kw)
        try:
            u = getattr(resp, "usage", None)
            tin = getattr(u, "input_tokens", 0) or 0
            tout = getattr(u, "output_tokens", 0) or 0
            cread = getattr(u, "cache_read_input_tokens", 0) or 0
            ccreate = getattr(u, "cache_creation_input_tokens", 0) or 0
            record_usage("anthropic", model, tin + ccreate, tout,
                         cost_usd=_price(model, tin, tout, cache_read=cread, cache_creation=ccreate),
                         cache_read_tokens=cread)
        except Exception:
            pass
        return resp


CLI_ERROR_SENTINEL = "__CLAUDE_CLI_ERROR__"
# H2: if the CLI session dies mid-batch, every call returns the sentinel and the
# agents treat it as an empty answer — grinding out garbage silently. Trip after
# this many CONSECUTIVE errors: abort loudly instead of corrupting a whole run.
_CB_MAX = int(os.environ.get("CLAUDE_CLI_CB_MAX", "5"))
_cli_consec_errors = 0


class ClaudeCliError(BaseException):
    """Raised when the CLI backend fails repeatedly — stop the run, don't guess.
    Derives from BaseException (not Exception) ON PURPOSE: agents wrap .create()
    in `except Exception` per item, which would swallow a normal exception and
    let the breaker grind on. BaseException propagates past those handlers so the
    run actually aborts (loudly) instead of producing a batch of garbage."""


class ClaudeCliClient:
    """Anthropic-shaped wrapper over `claude -p` (Claude Code CLI, headless).

    Uses Aviv's Claude SUBSCRIPTION auth (like `throng -p`) — no API tokens.
    Requires a one-time `claude /login`; until then calls return the CLI's
    "Not logged in" text and get_llm falls back to Ollama.
    """

    def __init__(self, model=None):
        self.model = model or os.environ.get("CLAUDE_CLI_MODEL", "")
        self.messages = self

    def create(self, model=None, max_tokens=1024, messages=None, system=None, **_):
        parts = []
        if system:
            parts.append(system)
        for m in (messages or []):
            c = m["content"]
            if isinstance(c, list):
                c = "".join(b.get("text", "") for b in c if isinstance(b, dict))
            parts.append(c)
        prompt = "\n\n".join(parts)
        # Honour the per-call model the scripts pick (Haiku = fast/light,
        # Opus = hard judgment) by mapping the API id to a CLI alias, so the
        # subscription runs the right tier instead of the CLI default on all.
        model_arg = self.model or _cli_model_alias(model)
        cmd = [_claude_bin(), "-p", prompt, "--output-format", "json"]
        if model_arg:
            cmd += ["--model", model_arg]
        global _cli_consec_errors
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            data = json.loads(out.stdout or "{}")
            if data.get("is_error"):
                raise RuntimeError(data.get("result") or "CLI returned is_error")
            # Log real token usage the CLI reports (subscription-covered spend).
            try:
                u = data.get("usage") or {}
                mu = data.get("modelUsage") or {}
                used_model = next(iter(mu), None) or (model_arg or model or "")
                record_usage(
                    "subscription", used_model,
                    (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0),
                    u.get("output_tokens") or 0,
                    cost_usd=data.get("total_cost_usd") or 0.0,
                    cache_read_tokens=u.get("cache_read_input_tokens") or 0)
            except Exception:
                pass
            _cli_consec_errors = 0  # a good call resets the breaker
            return _Resp(data.get("result", "") or "")
        except Exception as e:
            _cli_consec_errors += 1
            if _cli_consec_errors >= _CB_MAX:
                raise ClaudeCliError(
                    f"Claude CLI failed {_cli_consec_errors}x in a row (last: {e}). "
                    "Aborting run — check `claude` login / rate limit.") from e
            return _Resp(f"{CLI_ERROR_SENTINEL} {e}")


_CLI_READY_CACHE = pathlib.Path(__file__).resolve().parent.parent / "logs" / ".cli_ready.json"
_CLI_READY_TTL = 1800       # cache a HEALTHY (logged-in) result for 30 min
_CLI_NOTREADY_TTL = 120     # but re-probe a NOT-ready result after only 2 min


def claude_cli_ready() -> bool:
    # N2: the readiness check is itself a full ~20-30K-token `claude -p` call. The
    # nightly batch starts ~20 agent processes within minutes — cache the result so
    # they share ONE probe instead of each burning quota to ask "am I logged in?".
    # Asymmetric TTL: "logged out" is transient (a `claude /login` fixes it), so we
    # re-probe it quickly rather than stranding every agent on Ollama for 30 min
    # after login recovers.
    now = datetime.now(timezone.utc).timestamp()
    try:
        c = json.loads(_CLI_READY_CACHE.read_text())
        ttl = _CLI_READY_TTL if c.get("ready") else _CLI_NOTREADY_TTL
        if now - float(c.get("ts", 0)) < ttl:
            return bool(c.get("ready"))
    except Exception:
        pass
    ready = False
    try:
        out = subprocess.run([_claude_bin(), "-p", "ok", "--output-format", "json"],
                             capture_output=True, text=True, timeout=30)
        data = json.loads(out.stdout or "{}")
        ready = not data.get("is_error", True)
    except Exception:
        ready = False
    try:
        _CLI_READY_CACHE.parent.mkdir(exist_ok=True)
        _CLI_READY_CACHE.write_text(json.dumps({"ready": ready, "ts": now}))
    except Exception:
        pass
    return ready


def _cli_model_alias(model: str | None) -> str:
    """Map an Anthropic API model id (or alias) to a `claude --model` alias."""
    m = (model or "").lower()
    if "haiku" in m:
        return "haiku"
    if "sonnet" in m:
        return "sonnet"
    if "opus" in m:
        return "opus"
    return ""  # empty -> let the CLI use its own default model


class OllamaClient:
    """Anthropic-shaped wrapper over Ollama's native /api/chat. Free & local."""

    def __init__(self, model=None, url=None):
        self.model = model or os.environ.get("OLLAMA_MODEL", "qwen2.5:7b-instruct")
        self.url = (url or os.environ.get("OLLAMA_URL", "http://localhost:11434")).rstrip("/")
        self.messages = self  # so .messages.create(...) works

    def create(self, model=None, max_tokens=1024, messages=None, system=None, **_):
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        for m in (messages or []):
            c = m["content"]
            if isinstance(c, list):  # anthropic block form -> flatten to text
                c = "".join(b.get("text", "") for b in c if isinstance(b, dict))
            msgs.append({"role": m["role"], "content": c})
        payload = {
            "model": self.model, "messages": msgs, "stream": False,
            "options": {"num_predict": int(max_tokens), "temperature": 0.2},
        }
        req = urllib.request.Request(
            f"{self.url}/api/chat", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.load(r)
        try:  # local model — real tokens, $0 cost
            record_usage("ollama", self.model,
                         data.get("prompt_eval_count") or 0,
                         data.get("eval_count") or 0, cost_usd=0.0)
        except Exception:
            pass
        return _Resp(data.get("message", {}).get("content", ""))


class StrongLLMUnavailable(RuntimeError):
    """Raised by get_llm(require_strong=True) when no strong backend is available.

    High-stakes/destructive callers (e.g. LLM-judged contact merges) MUST use
    require_strong so they fail closed here rather than silently downgrading to
    the local 7B model. A weak model must never drive an irreversible merge.
    """



def _claude_bin() -> str:
    """Resolve the claude binary across machines: launchd gives python a bare
    PATH, and the CLI lives at ~/.local/bin on the MacBook but /opt/homebrew/bin
    on the mini. Env CLAUDE_BIN overrides."""
    import pathlib
    if os.environ.get("CLAUDE_BIN"):
        return os.environ["CLAUDE_BIN"]
    home = pathlib.Path.home()
    for p in (home / ".local/bin/claude", pathlib.Path("/opt/homebrew/bin/claude"),
              pathlib.Path("/usr/local/bin/claude")):
        if p.exists():
            return str(p)
    return "claude"

def get_llm(cfg: dict, *, require_strong: bool = False):
    """Return the configured LLM client. $0 backends preferred.

    LLM_BACKEND:
      'subscription' / 'claude_cli' -> `claude -p` (Aviv's subscription, free).
                       Falls back to Ollama if the CLI isn't logged in.
      'anthropic'   -> paid API (only if explicitly chosen).
      'ollama' / unset (default) -> local model, free.
      'auto'        -> subscription if logged in, else Ollama.

    require_strong=True: for high-stakes calls (destructive dedup merges). Returns
    ONLY the strong subscription backend; if `claude -p` is not logged in it
    RAISES StrongLLMUnavailable instead of downgrading to Ollama. Honours the $0
    rule — it never silently escalates to the paid API.
    """
    backend = os.environ.get("LLM_BACKEND", "subscription").lower()
    if backend in ("subscription", "claude_cli", "claude-cli", "auto"):
        if claude_cli_ready():
            return ClaudeCliClient()
        # 2026-07-16: a logged-out CLI now fails LOUDLY instead of silently
        # degrading to the local 7B - the small model once hallucinated a
        # contact's name during enrichment, which is exactly the class of
        # quiet corruption this repo exists to prevent. Escape hatch:
        # LLM_ALLOW_OLLAMA_FALLBACK=1 restores the old behaviour.
        if require_strong or os.environ.get("LLM_ALLOW_OLLAMA_FALLBACK") != "1":
            raise StrongLLMUnavailable(
                "claude -p not logged in on this machine — run `claude /login`. "
                "(Ollama fallback disabled by default; set "
                "LLM_ALLOW_OLLAMA_FALLBACK=1 to override temporarily.)")
        return OllamaClient()
    if backend == "anthropic":
        import anthropic
        return _RecordingAnthropic(anthropic.Anthropic(api_key=cfg["anthropic_api_key"]))
    if require_strong:
        raise StrongLLMUnavailable(f"LLM_BACKEND={backend} is not a strong backend")
    return OllamaClient()


def anthropic_paid_client(cfg: dict):
    """Explicit paid-API client with usage logging — for the few routines that
    intentionally use the API (e.g. learn_from_crm) rather than the subscription."""
    import anthropic
    return _RecordingAnthropic(anthropic.Anthropic(api_key=cfg["anthropic_api_key"]))


def ollama_ready(url=None) -> bool:
    url = (url or os.environ.get("OLLAMA_URL", "http://localhost:11434")).rstrip("/")
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False
