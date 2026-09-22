"""Epic H (#106): local, secret-scrubbed error logging (#107) and a
voluntary "share this error" package (#108).

Two small pieces of operational hygiene, nothing more:

1. Errors get logged locally so failures can be learned from. Structured,
   scoped, and pruned to a retention window, so the file does not grow
   forever and does not become an undeclared record of everything a player
   ever did. Nothing here reaches a network - this module makes no HTTP
   request of any kind - so nothing is uploaded anywhere by default.
2. If a player wants to help fix a specific bug, they can package that same
   scoped, scrubbed log into a pre-filled GitHub issue and review it before
   anything is sent. Sharing is their action, never this module's: the
   packaging function returns text and a URL, it does not open a browser,
   make a request, or otherwise transmit anything on its own.

WHAT GETS SCRUBBED. Anything shaped like a credential: an Anthropic key
(sk-ant-...), a generic long API-key-looking token, an Authorization/Bearer
header value, and a KEY=VALUE line for any *_API_KEY/*_TOKEN/*_SECRET name -
the same env vars fm9/planner.py and fm9/ai_settings.py already read
(ANTHROPIC_API_KEY, PLANNER_API_KEY, XAI_API_KEY, GROK_API_KEY, ...).
Scrubbing runs on write, so a secret that never should have been in a log
message is never persisted to begin with, not filtered out later on read.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

#: Where entries live. Overridable (tests use a tmp_path), local to the
#: machine, never a server. `~` so it survives outside any one repo clone.
DEFAULT_LOG_PATH = Path.home() / ".tonecommand" / "diagnostics.jsonl"

#: How long a scrubbed entry is worth keeping around to learn from before it
#: is just accumulated noise. Matches this project's own log-retention
#: convention elsewhere (14 days).
RETENTION_DAYS = 14

REPO = "monzta1/ToneCommand"

_SECRET_PATTERNS = [
    # Anthropic API keys: sk-ant-<...>
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    # generic long API-key-shaped tokens (OpenAI-style and similar)
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
    # an Authorization/Bearer header value
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-.=]{8,}"),
    # KEY=VALUE / "key": "VALUE" for anything credential-shaped by name
    re.compile(r'(?i)\b([A-Z][A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|AUTH_TOKEN))'
               r'(["\']?\s*[:=]\s*["\']?)([^\s"\',}]{4,})'),
]

REDACTED = "[REDACTED]"


def scrub_text(text: str) -> str:
    """Mask anything credential-shaped in a string. Never raises: diagnostics
    must never be the reason a real error is lost."""
    if not isinstance(text, str):
        return text
    out = text
    for pat in _SECRET_PATTERNS[:3]:
        out = pat.sub(REDACTED, out)
    # the KEY=VALUE pattern keeps the key name, redacts only the value
    out = _SECRET_PATTERNS[3].sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", out)
    return out


def scrub(obj):
    """Recursively scrub a string, dict, or list. Anything else (numbers,
    bools, None) is returned as-is; there is nothing secret-shaped in them."""
    if isinstance(obj, str):
        return scrub_text(obj)
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [scrub(v) for v in obj]
    return obj


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue  # a corrupt line is skipped, not fatal
    return out


def prune_expired(path: Path = DEFAULT_LOG_PATH, now: float | None = None,
                   retention_days: int = RETENTION_DAYS) -> int:
    """Drop entries older than the retention window. Returns how many were
    removed. Safe to call on an empty or missing file."""
    now = time.time() if now is None else now
    cutoff = now - retention_days * 86400
    entries = _load(path)
    kept = [e for e in entries if e.get("ts", 0) >= cutoff]
    removed = len(entries) - len(kept)
    if removed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(e) + "\n" for e in kept), encoding="utf-8")
    return removed


def log_error(scope: str, message: str, path: Path = DEFAULT_LOG_PATH,
              **context) -> dict:
    """Append one scrubbed, structured entry. `scope` names the subsystem
    (e.g. "planner", "server") so entries are filterable later; `context`
    is free-form extra detail (e.g. backend name, action kind) and is
    scrubbed exactly like `message`.

    Never raises: a failure to log a diagnostic must not become a second,
    unrelated error on top of the first one.
    """
    entry = {
        "ts": time.time(),
        "scope": scrub_text(str(scope)),
        "message": scrub_text(str(message)),
        "context": scrub(dict(context)),
    }
    try:
        prune_expired(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass
    return entry


def read_recent(scope: str | None = None, limit: int = 20,
                 path: Path = DEFAULT_LOG_PATH) -> list[dict]:
    """The most recent entries, newest first, optionally filtered by scope.
    Read-only; does not prune (log_error already prunes on write)."""
    entries = _load(path)
    if scope is not None:
        entries = [e for e in entries if e.get("scope") == scope]
    return list(reversed(entries))[:limit]


def package_for_sharing(scope: str | None = None, limit: int = 10,
                         path: Path = DEFAULT_LOG_PATH) -> dict:
    """Issue #108: build a pre-filled GitHub issue the player can REVIEW,
    never send. Every field here is already-scrubbed log content plus a
    defensive re-scrub, since a shared report is the one that leaves the
    machine and deserves the extra pass. This function performs no request
    of any kind; sending only happens if the player opens `url` themselves.
    """
    entries = read_recent(scope=scope, limit=limit, path=path)
    body_lines = ["Diagnostics shared voluntarily from ToneCommand.", ""]
    if not entries:
        body_lines.append("(no local diagnostics entries to include)")
    for e in entries:
        body_lines.append(f"- [{e.get('scope')}] {e.get('message')}")
        ctx = e.get("context") or {}
        if ctx:
            body_lines.append(f"  context: {json.dumps(ctx, sort_keys=True)}")
    body = scrub_text("\n".join(body_lines))
    title = scrub_text(f"Diagnostics report ({scope or 'all'}, {len(entries)} entries)")
    from urllib.parse import quote
    url = (f"https://github.com/{REPO}/issues/new"
           f"?title={quote(title)}&body={quote(body)}")
    return {"title": title, "body": body, "url": url, "entry_count": len(entries)}
