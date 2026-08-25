"""Which planner backend to use, chosen in the UI rather than in a file.

Selecting a backend used to mean editing .env and restarting the server, and
knowing which one answered meant reading a log. This holds the choice instead.

Deliberately does NOT change how the planner decides anything. The planner
already reads its configuration from the environment and from .env
(planner._env), so applying a saved choice means writing those same variables
into this process, and the planner behaves exactly as it does when configured
by hand. Precedence therefore falls out for free, highest first:

    the settings file  >  the environment (including .env)  >  built-in default

Each backend reads DIFFERENT variables, and two of them read none at all, so
settings are stored per backend and the UI is told which fields a backend
actually uses. Offering a box that silently does nothing is the same sin as
offering a backend that silently falls through to another one:

    cli     CLAUDE_CLI_MODEL.
    api     ANTHROPIC_API_KEY, CLAUDE_API_MODEL.
    grok    GROK_CLI_MODEL.
    openai  PLANNER_BASE_URL, PLANNER_MODEL, PLANNER_API_KEY (key optional).

API keys never travel outward: `public()` reports whether one exists and
never what it is.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import planner

#: CLIProxyAPI's default, prefilled for the OpenAI-compatible choice.
CLIPROXY_DEFAULT_URL = planner.CLIPROXY_DEFAULT_URL
LOCAL_LLM_DEFAULT_URL = "http://127.0.0.1:1234/v1"      # LM Studio

BACKEND_LABELS = {
    "": "auto (let the planner choose)",
    "cli": "Claude Code CLI",
    "api": "Claude API",
    "grok": "Grok CLI",
    "openai": "OpenAI-compatible endpoint",
}

#: Which controls each backend genuinely honours, and the variable behind each.
BACKEND_FIELDS = {
    # Auto reads the same three as openai: a configured router is the first
    # candidate the planner tries (#21), so a base URL still matters here.
    "": {"baseUrl": "PLANNER_BASE_URL", "model": "PLANNER_MODEL",
         "key": "PLANNER_API_KEY"},
    "cli": {"baseUrl": None, "model": "CLAUDE_CLI_MODEL", "key": None},
    "api": {"baseUrl": None, "model": "CLAUDE_API_MODEL",
            "key": "ANTHROPIC_API_KEY"},
    "grok": {"baseUrl": None, "model": "GROK_CLI_MODEL", "key": None},
    "openai": {"baseUrl": "PLANNER_BASE_URL", "model": "PLANNER_MODEL",
               "key": "PLANNER_API_KEY"},
}

#: Model boxes can always be left blank: every backend has a default. Keys do
#: not reduce to a per-backend flag, because the Claude API cannot run without
#: one while an OAuth router wants none, so the panel states the whole rule in
#: the field itself.
MODEL_ALWAYS_OPTIONAL = True

#: Said out loud in the UI, because "no model box" invites the question.
BACKEND_NOTES = {
    "": "A configured endpoint is tried first, then the Claude CLI, then the "
        "Claude API. Leave the endpoint blank to use the CLI.",
    "cli": "Runs on your Claude subscription. Model optional; blank uses "
           "the planner default.",
    "api": "Needs an Anthropic API key. Model optional; blank uses the "
           "planner default.",
    "grok": "Runs on your Grok subscription. Model optional; blank uses the "
            "CLI's own default.",
    "openai": "Any OpenAI-compatible server, including CLIProxyAPI and local "
              "models. A key is often not needed.",
}

_MANAGED = ("PLANNER_BACKEND", "PLANNER_BASE_URL", "PLANNER_MODEL",
            "PLANNER_API_KEY", "GROK_CLI_MODEL", "ANTHROPIC_API_KEY",
            "CLAUDE_CLI_MODEL", "CLAUDE_API_MODEL")


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
    models: dict = field(default_factory=dict)   # backend -> model
    keys: dict = field(default_factory=dict)     # backend -> key

    def model_for(self, backend: str | None = None) -> str:
        want = self.backend if backend is None else backend
        return self.models.get(want or "openai", "")

    def key_for(self, backend: str | None = None) -> str:
        want = self.backend if backend is None else backend
        return self.keys.get(want or "openai", "")

    def public(self) -> dict:
        """What the browser may see. Keys never leave this process."""
        return {"backend": self.backend, "baseUrl": self.base_url,
                "model": self.model_for(), "hasKey": bool(self.key_for())}


def _from_env() -> AiSettings:
    """Seed from however the user has configured things by hand."""
    return AiSettings(
        backend=planner._env("PLANNER_BACKEND").lower(),
        base_url=planner._env("PLANNER_BASE_URL"),
        models={k: v for k, v in
                (("openai", planner._env("PLANNER_MODEL")),
                 ("grok", planner._env("GROK_CLI_MODEL")),
                 ("cli", planner._env("CLAUDE_CLI_MODEL")),
                 ("api", planner._env("CLAUDE_API_MODEL"))) if v},
        keys={k: v for k, v in
              (("openai", planner._env("PLANNER_API_KEY")),
               ("api", planner._env("ANTHROPIC_API_KEY"))) if v},
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
        return settings                   # a corrupt file must not brick startup
    if not isinstance(stored, dict):
        return settings
    if isinstance(stored.get("backend"), str) and stored["backend"]:
        settings.backend = stored["backend"].lower()
    if isinstance(stored.get("baseUrl"), str) and stored["baseUrl"]:
        settings.base_url = stored["baseUrl"]
    for attr, key in (("models", "models"), ("keys", "keys")):
        blob = stored.get(key)
        if isinstance(blob, dict):
            merged = dict(getattr(settings, attr))
            merged.update({k: v for k, v in blob.items()
                           if isinstance(v, str) and v})
            setattr(settings, attr, merged)
    return settings


def save(patch: dict) -> AiSettings:
    """Apply a POST body and persist it.

    A blank or absent key KEEPS whatever is stored for that backend; removing
    one takes an explicit clearKey. Anything else and a user who edits the base
    URL loses their key without being told. Values are stored per backend, so
    an OpenAI router key can never quietly become an Anthropic one.
    """
    current = load()
    backend = str(patch.get("backend", current.backend) or "").lower()
    if backend and backend not in planner.BACKENDS:
        raise ValueError(f"unknown backend {backend!r}; "
                         f"expected one of {', '.join(planner.BACKENDS)}")
    fields = BACKEND_FIELDS.get(backend, {})
    slot = backend or "openai"          # auto shares openai's variables
    models, keys = dict(current.models), dict(current.keys)

    if fields.get("model"):
        if "model" in patch:
            new_model = str(patch.get("model") or "")
            models[slot] = new_model
            if not new_model:
                models.pop(slot, None)
    if fields.get("key"):
        if patch.get("clearKey"):
            keys.pop(slot, None)
        elif str(patch.get("apiKey") or ""):
            keys[slot] = str(patch["apiKey"])

    base_url = (str(patch.get("baseUrl", current.base_url) or "")
                if fields.get("baseUrl") else current.base_url)
    updated = AiSettings(backend=backend, base_url=base_url,
                         models=models, keys=keys)
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"backend": updated.backend, "baseUrl": updated.base_url,
         "models": updated.models, "keys": updated.keys}, indent=2) + "\n")
    apply_to_env(updated)
    return updated


def apply_to_env(settings: AiSettings | None = None) -> AiSettings:
    """Push the choice onto the planner's existing configuration surface.

    This is what makes a UI change take effect on the next prompt with no
    restart, without the planner needing to know this module exists. Only the
    variables the chosen backend reads are set; the rest are cleared, so a
    stale value cannot steer a backend it was never meant for.
    """
    settings = load() if settings is None else settings
    wanted = {}
    if settings.backend:
        wanted["PLANNER_BACKEND"] = settings.backend
    fields = BACKEND_FIELDS.get(settings.backend, {})
    if fields.get("baseUrl") and settings.base_url:
        wanted[fields["baseUrl"]] = settings.base_url
    if fields.get("model") and settings.model_for():
        wanted[fields["model"]] = settings.model_for()
    if fields.get("key") and settings.key_for():
        wanted[fields["key"]] = settings.key_for()
    for name in _MANAGED:
        if name in wanted:
            os.environ[name] = wanted[name]
        else:
            os.environ.pop(name, None)
    return settings


#: Aliases the Claude CLI documents for --model. Suggestions, not a whitelist:
#: full ids like claude-fable-5 are accepted too, so the box stays free text.
CLAUDE_CLI_ALIASES = ("sonnet", "opus", "haiku", "fable")


def list_models(backend: str) -> dict:
    """Model ids to offer for a backend, and where they came from.

    Suggestions only. Every model box stays typeable, because a list that
    cannot be overridden is worse than no list the moment it goes stale.
    """
    backend = (backend or "openai").lower()
    if backend == "grok":
        found, why = _grok_models()
    elif backend == "openai":
        found, why = _endpoint_models()
    elif backend == "cli":
        found, why = list(CLAUDE_CLI_ALIASES), "aliases the claude CLI documents"
    elif backend == "api":
        found, why = _anthropic_models()
    else:
        found, why = [], ""
    return {"backend": backend, "models": found, "source": why}


def _anthropic_models() -> tuple[list[str], str]:
    """Ask Anthropic, rather than shipping a list of ids that will age.

    That backend needs a key to run at all, so when one is configured there
    is nothing to save by guessing. With no key, offer the planner default
    and say that is what it is.
    """
    key = load().key_for("api") or planner._env("ANTHROPIC_API_KEY")
    if not key:
        return [planner.MODEL], "the planner default; add a key to list models"
    try:
        import anthropic
        listing = anthropic.Anthropic(api_key=key).models.list(limit=20)
        found = [m.id for m in listing.data if getattr(m, "id", None)]
    except Exception as exc:                    # offline, bad key, old SDK
        return ([planner.MODEL],
                f"could not list models ({type(exc).__name__}); showing the default")
    return (found, "the Anthropic models API") if found else (
        [planner.MODEL], "the API listed nothing; showing the default")


def _grok_models() -> tuple[list[str], str]:
    """Ask the grok CLI. It has a `models` subcommand for exactly this."""
    import subprocess
    binary = planner.find_grok_cli()
    if not binary:
        return [], "the grok binary is not on this machine"
    try:
        proc = subprocess.run([binary, "models"], capture_output=True,
                              text=True, timeout=20, cwd="/tmp",
                              env=planner.cli_env(planner.GROK_ENV_KEYS))
    except (subprocess.TimeoutExpired, OSError) as exc:
        return [], f"grok models failed: {exc}"
    found = []
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith(("*", "-")):
            name = stripped.lstrip("*- ").split(" ")[0].strip()
            if name:
                found.append(name)
    return found, "grok models" if found else (
        [], "grok models listed nothing")[1]


def _endpoint_models() -> tuple[list[str], str]:
    """Ask the configured endpoint. /models is part of the OpenAI shape."""
    import json as _json
    import urllib.error
    import urllib.request
    base = load().base_url or planner._openai_base_url()
    if not base:
        return [], "set a base URL first"
    req = urllib.request.Request(f"{base.rstrip('/')}/models", method="GET")
    key = load().key_for("openai")
    if key:
        req.add_header("authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, ValueError,
            _json.JSONDecodeError) as exc:
        return [], f"could not reach {base}: {exc}"
    entries = data.get("data") if isinstance(data, dict) else None
    found = [e["id"] for e in entries or []
             if isinstance(e, dict) and isinstance(e.get("id"), str)]
    return found, f"{base}/models" if found else "the endpoint listed no models"


def available_backends() -> list[dict]:
    """Which backends this host can run, which controls each one honours, and
    why an unusable one is unusable.

    A dead option that silently falls through to something else is worse than
    no option, and so is a control that silently does nothing.
    """
    settings = load()
    usable = {
        "openai": bool(settings.base_url),
        "cli": planner.find_claude_cli() is not None,
        "grok": planner.find_grok_cli() is not None,
        "api": bool(settings.keys.get("api")),
    }
    reasons = {
        "openai": "set a base URL to enable",
        "cli": "the claude binary is not on this machine",
        "grok": "the grok binary is not on this machine",
        "api": "needs an Anthropic API key",
    }
    out = []
    # "" (auto) is listed first and is always available: it is the behaviour a
    # fresh install has. It carries fields because a configured endpoint is the
    # planner's first candidate even with nothing pinned.
    for name in ("",) + tuple(planner.BACKENDS):
        fields = BACKEND_FIELDS[name]
        out.append({
            "backend": name, "label": BACKEND_LABELS[name],
            "available": usable.get(name, True),
            "why": "" if usable.get(name, True) else reasons[name],
            "note": BACKEND_NOTES[name],
            "needsBaseUrl": bool(fields["baseUrl"]),
            "needsModel": bool(fields["model"]),
            "needsKey": bool(fields["key"]),
            "modelOptional": MODEL_ALWAYS_OPTIONAL,
            "model": settings.models.get(name or "openai", ""),
            "hasKey": bool(settings.keys.get(name or "openai")),
        })
    return out
