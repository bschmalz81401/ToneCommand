"""Per-scene captures for the balance rules (issue #102 G3).

`capture_scenes` switches the unit through the scenes asked for, records
the test capture on each (capture.record, the G1 method), and comes back
to the scene it found loaded, whatever happens in between. It touches
nothing else: the caller has already put the routing in the re-amp state
under routing.temporary, and that context manager puts it back. Proven on
the simulator with a fake recorder; the live proof is the next rig
session's, and the issues say so.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import capture


class SoundCheckError(RuntimeError):
    """One line, written for the person at the rig."""


def capture_scenes(fm9: Any, scenes: list[int], recorder: Callable, directory: Path,
                   routing: Any = None) -> dict:
    """{origin_scene, captures: [{scene, name, wav, path}], returned: bool}.
    `recorder(signal, seconds, out_channel)` is reamp.replay_and_record on
    hardware and a fake in tests. Stops at the first scene that fails and
    still returns to the origin scene."""
    want = [int(s) for s in scenes]
    if any(not 1 <= s <= 8 for s in want):
        raise SoundCheckError("scenes are 1 to 8")
    current = fm9.scene_name()
    origin = current[0] if current else None
    if origin is None:
        raise SoundCheckError("the unit did not say which scene is loaded; nowhere to come back to")
    preset = fm9.current_preset()
    out: list[dict] = []
    returned = False
    try:
        for s in want:
            if s != fm9.scene_name()[0]:
                fm9.set_scene(s)
            name = fm9.scene_name(s)
            side = capture.record("test", directory, recorder=recorder, preset=preset,
                                  scene=s, routing=routing)
            out.append({"scene": s, "name": name[1] if name else None,
                        "wav": side["wav"], "path": side["path"],
                        "rms_dbfs": side["rms_dbfs"], "peak_dbfs": side["peak_dbfs"]})
    finally:
        now = fm9.scene_name()
        if now and now[0] != origin:
            fm9.set_scene(origin)
        after = fm9.scene_name()
        returned = bool(after and after[0] == origin)
    return {"origin_scene": origin, "captures": out, "returned": returned}


# --- G5: catch AND propose a fix, human-confirmed (#104) ------------------------

from . import measure as _measure  # noqa: E402

OUTPUT_FAMILY = "OUTPUT"
OUTPUT_SCENE_PID = {n: 17 + n for n in range(1, 9)}     # OUTPUT_SCENE1 = pid 18
TRIM_MIN, TRIM_MAX = -20.0, 20.0
MAX_ROUNDS = 3
#: The target for a scene, as LU against the rhythm median, from the policy's
#: own numbers: cleans and rhythm sit on it, a lead sits in the middle of the
#: rulebook's 2 to 3 LU band, and anything over the cap comes down to 3.
TARGETS = {"clean": 0.0, "rhythm": 0.0, "lead": 2.5, "other": 0.0}
OVER_CAP_TARGET = 3.0
AMP_CAVEAT = ("set the loudness at the amp DISTORT_LEVEL first; OUTPUT_SCENEn is a "
              "trim and is masked when the amp is the bottleneck (tone_rules.md)")


def _half_db(x: float) -> float:
    return round(x * 2) / 2


def propose(balance: dict, levels: dict[int, float]) -> dict:
    """One OUTPUT_SCENEn move per measurable finding, never sent.

    `balance` is measure.scene_balance's answer; `levels` the scenes'
    current OUTPUT_SCENEn trims in dB (the caller read them from the
    buffer). Targets are all against the same rhythm median, so two moves
    cannot fight; a scene appears once, the cap rule winning over the lead
    rule. Style findings and findings with no baseline get no move."""
    fixes: list[dict] = []
    unfixable: list[dict] = []
    if not balance.get("baseline"):
        for f in balance.get("findings") or []:
            unfixable.append({"scene": None, "rule": f.get("rule"), "line": f.get("line"),
                              "why": "no rhythm scene to judge against"})
        return {"summary": balance.get("note") or "nothing measurable to fix",
                "fixes": [], "actions": [], "unfixable": unfixable, "round": None}
    by_scene = {s["scene"]: s for s in balance.get("scenes") or []}
    seen: set[int] = set()
    # the cap rule first so it wins the scene
    ordered = sorted(balance.get("findings") or [],
                     key=lambda f: 0 if f.get("rule") == "cap_4lu_above_rhythm" else 1)
    for f in ordered:
        if f.get("cls") != "intent_target":
            continue
        try:
            scene = int(f["line"].split()[1])
        except (KeyError, IndexError, ValueError):
            unfixable.append({"scene": None, "rule": f.get("rule"), "line": f.get("line"),
                              "why": "the finding names no scene"})
            continue
        if scene in seen:
            continue
        row = by_scene.get(scene)
        if row is None or scene not in levels:
            unfixable.append({"scene": scene, "rule": f.get("rule"), "line": f.get("line"),
                              "why": "the scene's OUTPUT_SCENEn trim was not read"})
            continue
        role = row.get("role") or "other"
        target = OVER_CAP_TARGET if f.get("rule") == "cap_4lu_above_rhythm" else TARGETS.get(role, 0.0)
        move = _half_db(target - float(row["delta_lu"]))
        if move == 0.0:
            continue
        want = float(levels[scene]) + move
        value = max(TRIM_MIN, min(TRIM_MAX, want))
        clamped = value != want
        note = None
        if clamped:
            short = abs(want - value)
            note = f"the trim runs out {short:.1f} dB short of the move; {AMP_CAVEAT}"
        seen.add(scene)
        pname = f"OUTPUT_SCENE{scene}"
        fixes.append({
            "how": "actions",
            "label": f"Scene {scene} ({role}) {'+' if move > 0 else ''}{move:g} dB on {pname}",
            "reason": f.get("line"), "scene": scene, "rule": f.get("rule"),
            "delta_db": move, "clamped": clamped, "note": note,
            "actions": [{"kind": "set_param", "block": OUTPUT_FAMILY, "instance": 1,
                         "param": pname, "value": round(value, 2),
                         "reason": f"{f.get('line')}; {pname} {levels[scene]:g} -> {value:g} dB "
                                   f"({'+' if move > 0 else ''}{move:g}) against {balance.get('baseline')}"}],
        })
    actions = [a for fx in fixes for a in fx["actions"]]
    n = len(fixes)
    summary = (f"sound check: {n} move{'s' if n != 1 else ''} to balance the scenes"
               if n else "sound check: nothing measurable to fix")
    return {"summary": summary, "fixes": fixes, "actions": actions, "unfixable": unfixable,
            "round": None}


def new_state() -> dict:
    return {"round": 0, "history": [], "done": False, "ended_by": None}


def rounds(state: dict, balance: dict, proposal: dict) -> dict:
    """Advance the round state after a measurement and its proposal. The
    round number increments when there is something to propose; the run
    ends clean when no measurable finding remains, or by the cap after
    MAX_ROUNDS with findings left, and then the proposal carries no
    actions and says what remains."""
    findings = [f for f in balance.get("findings") or [] if f.get("cls") == "intent_target"]
    lufs = {s["scene"]: s["lufs"] for s in balance.get("scenes") or []}
    if not findings:
        state["done"], state["ended_by"] = True, "clean"
        proposal["summary"] = (f"sound check: balanced after {state['round']} round"
                               f"{'s' if state['round'] != 1 else ''}" if state["round"]
                               else "sound check: the scenes are balanced")
        proposal["actions"], proposal["fixes"] = [], []
    elif state["round"] >= MAX_ROUNDS:
        state["done"], state["ended_by"] = True, "cap"
        remain = "; ".join(f["line"] for f in findings)
        proposal["summary"] = f"after {MAX_ROUNDS} rounds these remain: {remain}"
        proposal["actions"], proposal["fixes"] = [], []
    elif proposal.get("actions"):
        state["round"] += 1
        proposal["round"] = state["round"]
    else:
        state["done"], state["ended_by"] = True, "cap"
        remain = "; ".join(f["line"] for f in findings)
        proposal["summary"] = f"nothing left to move; these remain: {remain}"
    state["history"].append({"round": state["round"], "findings": len(findings),
                             "proposed": len(proposal.get("actions") or []), "lufs": lufs})
    proposal["round"] = state["round"]
    return state
