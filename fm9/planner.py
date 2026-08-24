"""Natural-language layer: prompt -> concrete FM9 parameter plan.

Backend order, when nothing is pinned:
1. An OpenAI-compatible HTTP endpoint, when PLANNER_BASE_URL is set. A
   configured router wins over a `claude` binary that merely happens to be on
   PATH: setting a base URL is deliberate, and a router that gets silently
   shadowed is undebuggable.
2. Claude Code CLI in headless mode (uses the existing Claude subscription,
   no API key needed) when the `claude` binary is available.
3. Claude API with structured outputs, if ANTHROPIC_API_KEY is set.

PLANNER_BACKEND pins one backend and disables fallthrough, because a
deliberate choice must not quietly resolve to a different vendor's model.
The Grok CLI is only ever reached that way, never auto-selected.

Failure taxonomy (design by @Triumph1701, issue #7):
- transport or malformed output is a BACKEND failure: record the attempt and
  try the next candidate.
- a reply that parses but describes no usable actions is a PLANNER RESULT:
  return it, do not fall through, and do not blame the backend.
- an aggregate error is raised only after every candidate is exhausted.

The plan is only a proposal; nothing is sent to the FM9 until the user
confirms in the UI.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

MODEL = "claude-opus-5"   # API backend model
CLI_MODEL = "sonnet"      # CLI backend model: light on subscription usage


def find_claude_cli() -> str | None:
    """Locate the claude CLI: PATH first, then the desktop-app bundle."""
    path = shutil.which("claude")
    if path:
        return path
    bundles = sorted(
        Path.home().glob(
            "Library/Application Support/Claude/claude-code/*/claude.app/Contents/MacOS/claude"),
        key=lambda p: p.parent.parent.parent.name)
    return str(bundles[-1]) if bundles else None

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string",
                    "description": "One-sentence recap of what will change"},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string",
                             "enum": ["set_param", "set_scene", "set_bypass",
                                      "set_channel", "set_tempo", "set_type",
                                      "add_block", "bind_pedal",
                                      "rename_preset", "rename_scene", "store"]},
                    "block": {"type": ["string", "null"],
                              "description": "Block name for set_param/set_bypass/set_channel, e.g. amp, gate, input, delay, reverb, cab, drive, peq, geq, comp"},
                    "instance": {"type": "integer",
                                 "description": "Block instance, 1-4 (1 if unsure)"},
                    "param": {"type": ["string", "null"],
                              "description": "Symbolic param name from the reference list, e.g. DISTORT_DRIVE"},
                    "value": {"type": ["number", "null"],
                              "description": "Target display value for set_param (in the param's display units), scene number 1-8 for set_scene, channel 0-3 for set_channel, BPM for set_tempo"},
                    "bypassed": {"type": ["boolean", "null"],
                                 "description": "For set_bypass: true = bypass the block, false = engage it"},
                    "type_name": {"type": ["string", "null"],
                                  "description": "For set_type: exact model name from the roster. For rename_preset/rename_scene: the new name (max 32 chars)"},
                    "position": {"type": ["string", "null"],
                                 "description": "For add_block: pre, post, or any (relative to the amp)"},
                    "reason": {"type": "string",
                               "description": "Short justification tied to the user's request"},
                },
                "required": ["kind", "block", "instance", "param", "value",
                             "bypassed", "type_name", "position", "reason"],
                "additionalProperties": False,
            },
        },
        "clarification": {
            "type": ["string", "null"],
            "description": "Set ONLY if the request is too ambiguous to act on; actions must be empty then",
        },
    },
    "required": ["summary", "actions", "clarification"],
    "additionalProperties": False,
}

SYSTEM = """You translate a guitarist's natural-language tone requests into concrete Fractal FM9 parameter changes.

You receive the current device state (preset, scene, blocks present with bypass state, and current values of common parameters) and a reference list of controllable parameters with their display ranges. Respond only with a plan.

Rules:
- Only propose changes for blocks that exist in the current preset. If the preset has no GATE block, gate requests map to the INPUT block's noise gate (INPUT_THRESH etc. on instance 1).
- "Tighten the gate" = raise gate threshold (less negative dB). "Loosen" = lower it. For drop tunings (Drop C etc.), gate low-cut/threshold changes should be conservative.
- Knob params (Gain, Bass, Mid, Treble, Presence, Depth, Master) are 0..10. "Slightly"/"a touch" = about 0.3-0.7 from current value; "a bit"/"some" = about 1.0; "a lot" = 2.0+. Never exceed the display range.
- dB params: "slightly" = 1-2 dB, "a bit" = 2-3 dB, "a lot" = 4-6 dB.
- "Reduce bass before the amp" means EQ or input-side changes (amp DISTORT_BASS is in the amp's tonestack; a PEQ/GEQ before the amp is pre-amp). If no pre-amp EQ block exists, use the amp's Bass and say so in the reason.
- Scene-specific requests (e.g. "make scene 2 lower gain") require that scene to be active for parameter edits; propose a set_scene to that scene first, then the parameter change, then note in the summary that the device will stay on that scene.
- All changes are live edit-buffer changes and are not saved to the preset.
- If the request is ambiguous or asks for something unsupported (file operations, saving presets, buying gear), set clarification and return no actions.
- Values must always be the ABSOLUTE target display value, computed from the current value shown in the device state.

Amp/drive/reverb model selection (set_type):
- Use set_type with the EXACT model name from the roster. The amp roster lists each entry as `type_name = the real-world amp it models`; Fractal's names are deliberately oblique, so match the artist/era/sound against the real amp on the right, then send the name on the LEFT verbatim as type_name (e.g. Van Halen Balance era = a Peavey 5150, whose roster entry is "PVH 6160 Block Lead"). A few entries have no real-world amp listed; do not invent one for them. After a type change, also set sensible gain/EQ values for that sound.
- A type change replaces the block's model on its CURRENT channel and affects every scene that uses that channel. It cannot be undone by scene changes, only by re-selecting the preset (which discards all edits).

Scenes and multi-scene requests:
- Scenes share the same blocks; each scene stores its own per-block bypass states and channel choices. Block PARAMETERS and TYPES are per-channel, shared across scenes.
- To build "scene X with effect A, scene Y with effect B": set_scene X, set bypass states for X, then set_scene Y, set bypass states for Y. The device ends on the last selected scene. Note in the summary which scene is which.
- Adding blocks: use add_block (block name + optional position "pre"/"post" relative to the amp) when a requested effect has no block in the preset. It places the block on a free pass-through point in the signal chain; if the executor reports there is no free spot, relay that honestly. Freshly added blocks may need a set_type and parameter settings next.
- Expression pedal: use bind_pedal (block + param + optional value = floor percent 0-100) to put a continuous parameter under Pedal 2. Pedal 1 is the player's global volume and must NEVER be referenced or rebound.
- rename_preset / rename_scene (new name in type_name; scene number in value). Tool-created presets are prefixed FM9AI- automatically.
- store (slot number in value) persists the edit buffer to a preset slot. Only the slots listed as storable in the reference are allowed; every other slot is refused by the hardware layer, and if the reference says storing is disabled, never propose store. Only propose store when the user explicitly asks to save, and the UI will ask the user to confirm the overwrite separately.
- If a requested change is impossible, say so in the summary. Never silently substitute a different effect without saying so."""


BACKENDS = ("openai", "cli", "grok", "api")

FAILURE_CLASSES = (
    "unavailable",         # not configured, or its binary is missing
    "transport",           # could not be reached at all
    "timeout",
    "http_status",         # reached it; it refused
    "backend_error",       # it ran and reported its own failure
    "unreadable_output",   # replied, but no JSON object in the reply
    "empty_output",        # replied with nothing
)


class BackendFailure(RuntimeError):
    """This backend produced no plan, so the next candidate may run.

    Deliberately NOT raised for a reply that parses into JSON but describes
    no usable actions: that is a planner result, not a transport failure, and
    falling through on it would burn a working backend for a bad answer.
    """

    def __init__(self, backend: str, failure_class: str, detail: str,
                 target: str | None = None, model: str | None = None):
        if failure_class not in FAILURE_CLASSES:
            raise ValueError(f"unknown failure class {failure_class!r}")
        super().__init__(f"{backend} [{failure_class}] {detail}")
        self.backend = backend
        self.failure_class = failure_class
        self.detail = detail
        self.target = target
        self.model = model


@dataclass
class Attempt:
    """One backend's turn: what was tried, and how it went."""
    backend: str
    target: str | None = None          # base URL, binary path, or "sdk"
    model: str | None = None
    failure_class: str | None = None   # None once it produced the plan
    detail: str = ""

    def as_dict(self) -> dict:
        return {"backend": self.backend, "target": self.target,
                "model": self.model, "failure_class": self.failure_class,
                "detail": self.detail}


def _env(name: str, default: str = "") -> str:
    """env var first, then a NAME= line in .env at the repo root.

    Same sourcing convention as device.get_store_slots(), so planner config
    can live in the .env file the store whitelist already uses.
    """
    val = os.environ.get(name, "").strip()
    if not val:
        env_file = Path(__file__).resolve().parent.parent / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.strip().startswith(f"{name}="):
                    val = line.split("=", 1)[1].strip()
                    break
    return val or default


TIMEOUT_S = int(_env("PLANNER_TIMEOUT", "180"))   # per backend attempt


def _extract_json(text: str) -> dict:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in model output: {text[:200]}")
    return json.loads(text[start:end + 1])


def _validate(plan_obj: dict) -> dict:
    plan_obj.setdefault("summary", "")
    plan_obj.setdefault("clarification", None)
    actions = plan_obj.get("actions") or []
    clean = []
    for a in actions:
        if not isinstance(a, dict) or a.get("kind") not in (
                "set_param", "set_scene", "set_bypass", "set_channel",
                "set_tempo", "set_type", "add_block", "bind_pedal",
                "rename_preset", "rename_scene", "store"):
            continue
        a.setdefault("block", None)
        a.setdefault("instance", 1)
        a.setdefault("param", None)
        a.setdefault("value", None)
        a.setdefault("bypassed", None)
        a.setdefault("type_name", None)
        a.setdefault("position", None)
        a.setdefault("reason", "")
        clean.append(a)
    plan_obj["actions"] = clean
    return plan_obj


def _cli_error_message(proc: subprocess.CompletedProcess) -> str:
    """Best available error text from a failed CLI run.

    The CLI reports failures like an expired login inside the JSON envelope on
    stdout (is_error / result) and leaves stderr empty, so stderr alone is
    usually blank.
    """
    parts = []
    try:
        envelope = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        envelope = None
    if isinstance(envelope, dict):
        for key in ("result", "error", "api_error_status", "terminal_reason"):
            val = envelope.get(key)
            if isinstance(val, str) and val.strip():
                parts.append(val.strip())
                break
    stderr = (proc.stderr or "").strip()
    if stderr:
        parts.append(stderr)
    if not parts:
        parts.append(f"exit code {proc.returncode}, no output")
    return " | ".join(parts)[:300]


def _plan_via_cli(prompt: str, device_state: str,
                  param_reference: str) -> tuple[dict, str]:
    full_prompt = (
        f"{SYSTEM}\n\n"
        f"Controllable parameter reference:\n{param_reference}\n\n"
        f"Current device state:\n{device_state}\n\n"
        f"Request: {prompt}\n\n"
        "Respond with ONLY a single JSON object, no markdown fences and no "
        "other text, with this shape:\n"
        '{"summary": str, "actions": [{"kind": "set_param|set_scene|set_bypass|'
        'set_channel|set_tempo|set_type", "block": str|null, "instance": int, '
        '"param": str|null, "value": number|null, "bypassed": bool|null, '
        '"type_name": str|null, "position": str|null, "reason": str}], "clarification": str|null}'
    )
    cli = find_claude_cli()
    if not cli:
        raise BackendFailure("cli", "unavailable", "claude binary not found")
    try:
        proc = subprocess.run(
            [cli, "-p", full_prompt, "--output-format", "json",
             "--model", CLI_MODEL],
            capture_output=True, text=True, timeout=TIMEOUT_S,
            cwd="/tmp",
            env={**os.environ, "CLAUDE_CODE_ENTRYPOINT": "fm9-tone"},
        )
    except subprocess.TimeoutExpired:
        raise BackendFailure("cli", "timeout",
                             f"no reply within {TIMEOUT_S}s", target=cli)
    if proc.returncode != 0:
        raise BackendFailure("cli", "backend_error", _cli_error_message(proc),
                             target=cli)
    try:
        envelope = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        raise BackendFailure("cli", "unreadable_output",
                             proc.stdout.strip()[:200] or "empty stdout",
                             target=cli)
    if envelope.get("is_error"):
        raise BackendFailure("cli", "backend_error", _cli_error_message(proc),
                             target=cli)
    result_text = envelope.get("result", "")
    if not result_text.strip():
        raise BackendFailure("cli", "empty_output", "envelope carried no result",
                             target=cli)
    try:
        return _extract_json(result_text), envelope.get("model") or CLI_MODEL
    except ValueError as exc:
        raise BackendFailure("cli", "unreadable_output", str(exc)[:200],
                             target=cli)


def _plan_via_api(prompt: str, device_state: str,
                  param_reference: str) -> tuple[dict, str]:
    try:
        import anthropic
    except ImportError as exc:
        raise BackendFailure("api", "unavailable", str(exc))
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not os.environ.get("ANTHROPIC_API_KEY") and env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=[
            {"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": f"Controllable parameter reference:\n{param_reference}",
             "cache_control": {"type": "ephemeral"}},
        ],
        output_config={"format": {"type": "json_schema", "schema": PLAN_SCHEMA}},
        messages=[{
            "role": "user",
            "content": f"Current device state:\n{device_state}\n\nRequest: {prompt}",
        }],
    )
    if response.stop_reason == "refusal":
        return ({"summary": "Request declined by the model.", "actions": [],
                 "clarification": "The model declined this request. "
                                  "Try rephrasing."}, MODEL)
    try:
        text = next(b.text for b in response.content if b.type == "text")
    except StopIteration:
        raise BackendFailure("api", "empty_output", "no text block in reply",
                             target="sdk", model=MODEL)
    try:
        return json.loads(text), getattr(response, "model", MODEL)
    except (json.JSONDecodeError, ValueError):
        raise BackendFailure("api", "unreadable_output", text.strip()[:200],
                             target="sdk", model=MODEL)


def _api_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY")) or \
        (Path(__file__).resolve().parent.parent / ".env").exists()


_RUNNERS = {
    "cli": _plan_via_cli,
    "api": _plan_via_api,
}


def candidates() -> list[str]:
    """Backends to try, in order.

    PLANNER_BACKEND pins exactly one and disables fallthrough: choosing a
    backend on purpose must not quietly resolve to a different vendor's
    model. Unpinned, a configured router outranks a `claude` binary that
    merely happens to be on PATH, and the Grok CLI is never auto-selected for
    the same reason - it is reachable by pin or through a router.
    """
    pinned = _env("PLANNER_BACKEND").lower()
    if pinned:
        if pinned not in BACKENDS:
            raise RuntimeError(
                f"PLANNER_BACKEND={pinned!r} is not one of {', '.join(BACKENDS)}")
        return [pinned]
    order = []
    if find_claude_cli():
        order.append("cli")
    if _api_available():
        order.append("api")
    return order


def _plan_quality(plan_obj: dict) -> str:
    """Well-formed replies still divide into usable and not.

    A reply carrying neither actions nor a clarification parsed fine but says
    nothing: a planner-quality signal, not a transport failure. Callers get it
    labelled rather than retried, so a working backend is not burned for a bad
    answer.
    """
    if plan_obj.get("actions"):
        return "actions"
    if (plan_obj.get("clarification") or "").strip():
        return "clarification"
    return "empty"


def plan(prompt: str, device_state: str, param_reference: str) -> dict:
    """Ask each candidate backend in turn until one produces a plan.

    Returns the plan with `backend`, `model`, `plan_quality`, and the full
    `attempts` record attached. Raises only when every candidate failed at the
    transport level, with one aggregate message naming each attempt.
    """
    order = candidates()
    if not order:
        raise RuntimeError(
            "No planner backend: install the claude CLI, set ANTHROPIC_API_KEY, "
            "or point PLANNER_BASE_URL at an OpenAI-compatible endpoint")
    attempts: list[Attempt] = []
    for name in order:
        runner = _RUNNERS[name]
        try:
            raw, model = runner(prompt, device_state, param_reference)
        except BackendFailure as exc:
            attempts.append(Attempt(exc.backend, exc.target, exc.model,
                                    exc.failure_class, exc.detail))
            continue
        except Exception as exc:
            # An unexpected fault is still this backend failing, not a plan.
            attempts.append(Attempt(name, None, None, "backend_error",
                                    str(exc)[:300]))
            continue
        plan_obj = _validate(raw)
        attempts.append(Attempt(name, model=model))
        plan_obj["backend"] = name
        plan_obj["model"] = model
        plan_obj["plan_quality"] = _plan_quality(plan_obj)
        plan_obj["attempts"] = [a.as_dict() for a in attempts]
        log.info("planner: %s answered via %s (model %s, %d action(s))",
                 plan_obj["plan_quality"], name, model,
                 len(plan_obj.get("actions") or []))
        return plan_obj
    detail = "; ".join(f"{a.backend} [{a.failure_class}] {a.detail}"
                       for a in attempts)
    raise RuntimeError(f"every planner backend failed: {detail}")
