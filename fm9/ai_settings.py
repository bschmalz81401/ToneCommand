"""Which planner backend to use, chosen in the UI rather than in a file.

Selecting a backend used to mean editing .env and restarting the server, and
knowing which one answered meant reading a log. This holds the choice instead.

Deliberately does NOT change how the planner decides anything. The planner
already reads its configuration from the environment and from .env
(planner._env), so applying a saved choice means writing those same variables
into this process, and the planner behaves exactly as it does when configured
by hand. Precedence therefore falls out for free, highest first:

    the settings file  >  the environment (including .env)  >  built-in default

The API key is the one value that must never travel outward: `public()` reports
whether a key exists and never what it is.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from . import planner

#: CLIProxyAPI's default, prefilled for the OpenAI-compatible choice.
CLIPROXY_DEFAULT_URL = planner.CLIPROXY_DEFAULT_URL
LOCAL_LLM_DEFAULT_URL = "http://127.0.0.1:1234/v1"      # LM Studio

BACKEND_LABELS = {
    "cli": "Claude Code CLI",
    "api": "Claude API",
    "grok": "Grok CLI",
    "openai": "OpenAI-compatible endpoint",
}


def settings_path() -> Path:
    """Where the choice is stored. Gitignored; overridable for tests."""
    override = os.environ.get("TONECOMMAND_AI_SETTINGS", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "ai_settings.json"


@dataclass
class AiSettings:
    backend: str = ""          # "" means "let the planner decide as it always has"
    base_url: str = ""
    model: str = ""
    api_key: str = ""

    def public(self) -> dict:
        """What the browser may see. The key never leaves this process."""
        return {"backend": self.backend, "baseUrl": self.base_url,
                "model": self.model, "hasKey": bool(self.api_key)}


def _from_env() -> AiSettings:
    return AiSettings(
        backend=planner._env("PLANNER_BACKEND").lower(),
        base_url=planner._env("PLANNER_BASE_URL"),
        model=planner._env("PLANNER_MODEL"),
        api_key=planner._env("PLANNER_API_KEY"),
    )


def load() -> AiSettings:
    """The stored choice, falling back to the environment then the default."""
    settings = _from_env()
    path = settings_path()
    if not path.exists():
        return settings
    try:
        stored = json.loads(path.read_text())
    except (json.JSONDecodeError, ValueError, OSError):
        return settings                       # a corrupt file must not brick startup
    if not isinstance(stored, dict):
        return settings
    for attr, key in (("backend", "backend"), ("base_url", "baseUrl"),
                      ("model", "model"), ("api_key", "apiKey")):
        val = stored.get(key)
        if isinstance(val, str) and val:
            setattr(settings, attr, val.lower() if attr == "backend" else val)
    return settings


def save(patch: dict) -> AiSettings:
    """Apply a POST body and persist it.

    A blank or absent key KEEPS whatever is stored; removing one takes an
    explicit clearKey. Anything else and a user who edits the base URL loses
    their key without being told.
    """
    current = load()
    backend = str(patch.get("backend", current.backend) or "").lower()
    if backend and backend not in planner.BACKENDS:
        raise ValueError(f"unknown backend {backend!r}; "
                         f"expected one of {', '.join(planner.BACKENDS)}")
    updated = AiSettings(
        backend=backend,
        base_url=str(patch.get("baseUrl", current.base_url) or ""),
        model=str(patch.get("model", current.model) or ""),
        api_key="" if patch.get("clearKey") else
                (str(patch.get("apiKey") or "") or current.api_key),
    )
    body = {"backend": updated.backend, "baseUrl": updated.base_url,
            "model": updated.model, "apiKey": updated.api_key}
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2) + "\n")
    apply_to_env(updated)
    return updated


def apply_to_env(settings: AiSettings | None = None) -> AiSettings:
    """Push the choice onto the planner's existing configuration surface.

    This is what makes a UI change take effect on the next prompt with no
    restart, without the planner needing to know this module exists.
    """
    settings = load() if settings is None else settings
    for name, value in (("PLANNER_BACKEND", settings.backend),
                        ("PLANNER_BASE_URL", settings.base_url),
                        ("PLANNER_MODEL", settings.model),
                        ("PLANNER_API_KEY", settings.api_key)):
        if value:
            os.environ[name] = value
        else:
            os.environ.pop(name, None)
    return settings


def available_backends() -> list[dict]:
    """Which backends this host can actually run, in the planner's own order.

    A dead option that silently falls through to something else is worse than
    no option, so the panel is told what is real rather than what exists.
    """
    settings = load()
    usable = {
        "openai": bool(settings.base_url or planner._openai_base_url()),
        "cli": planner.find_claude_cli() is not None,
        "grok": planner.find_grok_cli() is not None,
        "api": planner._api_available(),
    }
    reasons = {
        "openai": "set a base URL to enable",
        "cli": "the claude binary is not on this machine",
        "grok": "the grok binary is not on this machine",
        "api": "no ANTHROPIC_API_KEY configured",
    }
    return [{"backend": name, "label": BACKEND_LABELS[name],
             "available": ok, "why": "" if ok else reasons[name]}
            for name, ok in ((b, usable[b]) for b in planner.BACKENDS)]
