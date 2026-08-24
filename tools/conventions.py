"""Optional local preset-convention config for the audit/report tools.

The public tools ship with no opinions about how presets should be laid
out or leveled: those are the OWNER'S rules, not the project's. If a
kb/conventions.json exists (kb/ is local-only, never committed), the
tools enforce it; without one they report facts and skip every
convention-based flag.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load() -> dict:
    p = ROOT / "kb" / "conventions.json"
    if not p.exists():
        return {}
    return json.load(p.open())
