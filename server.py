#!/usr/bin/env python3
"""FM9 natural-language tone controller - local web server.

Run:  .venv/bin/python server.py   then open http://127.0.0.1:8909

Safety contract: edit-buffer only. No store/save command is implemented;
nothing is ever written to a preset slot on the unit.
"""
from __future__ import annotations

import threading
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from fm9.device import FM9, FM9NotFound
from fm9.registry import Registry
from fm9 import ai_settings, editbuffer, health, planner
from fm9 import protocol as proto

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="FM9 Tone Control")

reg = Registry()
_lock = threading.Lock()
# Planner configuration lives in os.environ, which the settings panel rewrites
# and the planner rereads inside each backend runner. A save landing mid-plan
# could therefore tear the view: candidates() has already chosen a backend,
# then the runner picks up the new key and the old URL (@Triumph1701 on #25).
# Held for the whole planner call, and a save that cannot get it says so
# rather than hanging for the length of a plan.
_settings_lock = threading.Lock()
_fm9: FM9 | None = None

FRIENDLY = {"DISTORT": "Amp", "CABINET": "Cab", "FUZZ": "Drive", "GATE": "Gate",
            "INPUT": "Input", "OUTPUT": "Output", "COMP": "Compressor",
            "GEQ": "Graphic EQ", "PEQ": "Parametric EQ", "REVERB": "Reverb",
            "DELAY": "Delay", "CHORUS": "Chorus", "FLANGER": "Flanger",
            "PHASER": "Phaser", "WAH": "Wah", "PITCH": "Pitch",
            "FILTER": "Filter", "VOLUME": "Volume", "TREMOLO": "Tremolo",
            "FDBKSEND": "Send", "FDBKRET": "Return", "PLEX": "Plex Delay",
            "MULTITAP": "Multitap", "ROTARY": "Rotary", "LOOPER": "Looper"}

# Params surfaced to the planner and read for the state snapshot, per family.
INTEREST = {
    "DISTORT": [11, 12, 13, 14, 15, 26, 30, 1],
    "INPUT": [0, 1, 2, 3],
    "GATE": [0, 1, 3, 9],
    "FUZZ": [0, 1, 2, 3],
    "GEQ": list(range(0, 10)) + [11],
    "PEQ": list(range(0, 5)),
    "DELAY": [0, 1, 12, 32],
    "REVERB": [0, 1, 11],
    "PHASER": [2, 5, 6, 11, 12],
    "FLANGER": [1, 3, 4, 11, 12],
    "CHORUS": [2, 4, 10, 11],
    "WAH": [6, 10],
    "TREMOLO": [2, 3, 7],
    "ROTARY": [0, 5, 6],
}


def get_fm9() -> FM9:
    global _fm9
    if _fm9 is None:
        import os
        if os.environ.get("TONECOMMAND_SIM") == "1":
            from fm9.sim import SimFM9
            _fm9 = SimFM9(reg)     # virtual device: UI/planner dev offline
        else:
            _fm9 = FM9(reg)
    return _fm9


def drop_fm9():
    global _fm9
    if _fm9 is not None:
        try:
            _fm9.close()
        except Exception:
            pass
        _fm9 = None


def param_reference() -> str:
    """Static text listing controllable params, for the planner (cacheable)."""
    lines = []
    for fam, pids in INTEREST.items():
        for pid in pids:
            s = reg.spec(fam, pid)
            if s.dmin is None:
                continue
            label = s.label or s.name
            lines.append(f"{s.name} (block={FRIENDLY.get(fam, fam).lower()}, "
                         f"\"{label}\", {s.dmin}..{s.dmax} {s.unit or ''}, {s.scale})")
    lines.append("Scenes: 1-8 via set_scene. Block bypass via set_bypass. "
                 "Block channel A-D (0-3) via set_channel. Tempo via set_tempo.")
    from fm9.device import get_store_slots
    _slots = sorted(get_store_slots())
    lines.append(f"Storable slots (store action): "
                 f"{_slots[0]}-{_slots[-1]}" if _slots else
                 "Storing is DISABLED on this install (no slots configured); never propose store.")
    lines.append("\nAmp models selectable via set_type (block=amp). One per line as "
                 "`type_name = the real-world amp it models`; use the name to the "
                 "LEFT of the '=' as type_name, verbatim:")
    lines.extend(reg.amp_description(o) for o in reg.amp_roster)
    lines.append("\nDrive models selectable via set_type (block=drive). One per line as "
                 "`type_name = the real pedal it models` where known; use the LEFT name "
                 "verbatim as type_name. Entries without an '=' have no confirmed "
                 "real-world mapping; do not invent one:")
    lines.extend(reg.drive_description(o) for o in reg.drive_roster)
    et = reg.effect_type_models
    if et:
        lines.append("\nDelay/chorus type real-world references (NAME-keyed; "
                     "types cannot be SET yet, use for describing and "
                     "recommending only):")
        for section in ("delay_types", "chorus_types", "multitap_types"):
            for name, model in (et.get(section) or {}).items():
                lines.append(f"{name} = {model}")
    lines.append("\nReverb types selectable via set_type (block=reverb):")
    lines.append(", ".join(str(v) for v in reg.reverb_roster.values()))
    if reg.dynacabs:
        lines.append("\nDynaCab cabinets and the real cabs they capture "
                     "(cab selection is NOT a plannable action yet; use "
                     "only to describe or recommend, never to propose a "
                     "set):")
        for name, rec in reg.dynacabs.items():
            model = rec.get("model")
            lines.append(f"{name} = {model}" if model else name)
    return "\n".join(lines)


PARAM_REFERENCE = param_reference()


def shared_scenes(fm9: FM9) -> dict:
    """For each block, the scenes currently using each of its channels.

    The FM9 stores bypass and channel per scene, but block PARAMETERS live on
    the CHANNEL. So "make this scene grittier" moves every other scene sitting
    on that channel too, and nothing in the UI showed that before.

    COSTS A SCENE SWEEP. There is no way to read another scene's channel
    assignments without visiting it, so this walks all eight and returns to
    where it started. That is audible, so it must NEVER run on the state poll:
    it is called deliberately and cached per preset. Any scene that does not
    answer is skipped rather than guessed at.
    """
    here = fm9.scene_name()
    active = here[0] if here else 1
    by_block: dict = {}
    try:
        for sc in range(1, 9):
            try:
                fm9.set_scene(sc)
                blocks = fm9.status_dump() or []
            except Exception:
                continue
            for b in blocks:
                key = str(b.effect_id)
                by_block.setdefault(key, {}).setdefault(str(b.channel), []).append(sc)
    finally:
        try:
            fm9.set_scene(active)
        except Exception:
            pass
    return by_block


def scene_names(fm9: FM9) -> list[dict]:
    """Names of all eight scenes, for labelling the UI's scene buttons.

    Queried by number, so the loaded scene is untouched. A scene that does
    not answer is reported as None rather than guessed at or skipped, so the
    button still renders and says nothing it cannot back up.
    """
    out = []
    for n in range(1, 9):
        try:
            got = fm9.scene_name(n)
        except Exception:
            got = None
        out.append({"number": n, "name": got[1] if got else None})
    return out


def snapshot(fm9: FM9) -> dict:
    preset = fm9.current_preset()
    scene = fm9.scene_name()
    blocks = fm9.status_dump() or []
    out_blocks = []
    values = {}
    # What each value IS, so the UI can offer a control instead of a readout.
    # The browser used to carry its own table of maxima, which meant it could
    # only draw the seven amp knobs it knew about and drew every one of them
    # as though it ran 0-10. Ranges belong to the registry, so they are sent
    # from here and the UI stops guessing.
    meta = {}
    seen_fams = set()
    for b in blocks:
        fam = reg.family_of_effect_id(b.effect_id)
        if not fam:
            continue
        fname, inst = fam
        label = f"{FRIENDLY.get(fname, fname)} {inst}"
        out_blocks.append({"family": fname, "instance": inst, "label": label,
                           "bypassed": b.bypassed, "channel": "ABCD"[b.channel],
                           # the UI needs these to drive the block directly
                           "effect_id": b.effect_id,
                           "channel_index": b.channel,
                           "channels": max(1, b.channels_supported)})
        if fname == "CABINET" and "cab" not in values:
            vals = fm9.bulk_read(b.effect_id)
            if vals:
                chans = max(1, fm9._channels.get(b.effect_id, 1))
                stride = len(vals) // chans if chans > 1 else len(vals)
                base = min(b.channel, chans - 1) * stride
                bank, slot = vals[base + 0], vals[base + 4]
                values["cab"] = reg.cab_description(slot, bank)
        if fname in INTEREST and fname not in seen_fams:
            seen_fams.add(fname)
            vals = fm9.bulk_read(reg.effect_id(fname, inst))
            if vals:
                chans = max(1, fm9._channels.get(reg.effect_id(fname, inst), 1))
                stride = len(vals) // chans if chans > 1 else len(vals)
                base = min(b.channel, chans - 1) * stride
                if fname == "DISTORT" and base + 10 < len(vals):
                    values["AMP_MODEL"] = reg.amp_roster.get(
                        str(vals[base + 10]), f"ordinal {vals[base + 10]}")
                for pid in INTEREST[fname]:
                    s = reg.spec(fname, pid, inst)
                    idx = base + pid
                    if s.dmin is not None and idx < len(vals):
                        from fm9.protocol import normalized_to_display
                        values[f"{s.name}"] = round(
                            normalized_to_display(vals[idx] / 65534, s.dmin, s.dmax, s.scale), 2)
                        meta[s.name] = {
                            "family": fname, "instance": inst, "param": s.name,
                            "min": s.dmin, "max": s.dmax, "scale": s.scale,
                            "unit": s.unit or "",
                            # the label the FM9 itself uses, so the panel reads
                            # like the unit rather than like our variable names
                            "label": (s.label or s.name.split("_", 1)[-1]),
                        }
    return {
        "connected": True,
        # label, not just number: the wire numbers presets 0-511 and every
        # tool the owner cross-checks against numbers them 1-512.
        "preset": ({"number": preset[0], "editor": proto.editor_number(preset[0]),
                    "label": proto.slot_label(preset[0]), "name": preset[1]}
                   if preset else None),
        "scene": {"number": scene[0], "name": scene[1]} if scene else None,
        # All eight names so the UI can label its scene buttons with what the
        # owner called them rather than with the numbers 1-8. Read-only: this
        # queries names, it does not switch the active scene.
        "scenes": scene_names(fm9),
        "blocks": out_blocks,
        "values": values,
        "params": meta,
    }


def state_text(snap: dict) -> str:
    p, s = snap.get("preset"), snap.get("scene")
    lines = []
    if p:
        lines.append(f"Preset {p.get('label', p['number'])}: \"{p['name']}\"")
    if s:
        lines.append(f"Scene {s['number']}: \"{s['name']}\"")
    lines.append("Blocks in preset: " + ", ".join(
        f"{b['label']}{' (bypassed)' if b['bypassed'] else ''} ch{b['channel']}"
        for b in snap["blocks"]))
    lines.append("Current values: " + ", ".join(
        f"{k}={v}" for k, v in snap["values"].items()))
    return "\n".join(lines)


class PromptBody(BaseModel):
    prompt: str


class Action(BaseModel):
    kind: str
    block: str | None = None
    instance: int = 1
    param: str | None = None
    value: float | None = None
    bypassed: bool | None = None
    type_name: str | None = None   # model name for set_type; new name for renames
    position: str | None = None    # add_block: "pre" | "post" | "any" (vs amp)
    reason: str = ""


# family -> (type param id, roster attribute)
TYPE_PARAMS = {"DISTORT": (10, "amp_roster"), "FUZZ": (0, "drive_roster"),
               "REVERB": (10, "reverb_roster")}


def resolve_type_ordinal(family: str, name: str) -> tuple[int, str] | None:
    pid, roster_attr = TYPE_PARAMS.get(family, (None, None))
    if pid is None:
        return None
    roster: dict = getattr(reg, roster_attr)
    needle = name.strip().lower()
    for ordinal, label in roster.items():
        if str(label).lower() == needle:
            return (int(ordinal), str(label))
    matches = [(int(o), str(l)) for o, l in roster.items()
               if needle in str(l).lower()]
    if matches:
        return min(matches, key=lambda m: len(m[1]))
    if family == "FUZZ":
        by_model = [(int(o), str(roster.get(o, o)))
                    for o, rec in reg.drive_models.items()
                    if needle in str(rec.get("model", "")).lower()]
        if len(by_model) == 1:
            return by_model[0]
    if family == "DISTORT":
        # The planner sees amps as "Fractal name = real amp"; accept the right
        # hand side too, in case it answers with the amp it was actually after.
        by_model = [(int(o), str(roster.get(o, o)))
                    for o, rec in reg.amp_models.items()
                    if needle in str(rec.get("model", "")).lower()]
        if len(by_model) == 1:
            return by_model[0]
    tokens = set(needle.split())
    scored = [(len(tokens & set(str(l).lower().split())), int(o), str(l))
              for o, l in roster.items()]
    best = max(scored)
    return (best[1], best[2]) if best[0] > 0 else None


class ApplyBody(BaseModel):
    actions: list[Action]
    expected_preset: int | None = None


@app.get("/")
def index():
    return FileResponse(ROOT / "ui" / "index.html")


@app.get("/api/state")
def api_state():
    with _lock:
        try:
            snap = snapshot(get_fm9())
            return snap
        except FM9NotFound:
            drop_fm9()
            return {"connected": False}
        except Exception as e:
            drop_fm9()
            return JSONResponse({"connected": False, "error": str(e)}, status_code=500)


@app.post("/api/plan")
def api_plan(body: PromptBody):
    with _lock:
        try:
            snap = snapshot(get_fm9())
        except FM9NotFound:
            drop_fm9()
            return JSONResponse({"error": "FM9 not connected"}, status_code=503)
    try:
        with _settings_lock:
            result = planner.plan(body.prompt, state_text(snap), PARAM_REFERENCE)
        result["device"] = {"preset": snap["preset"], "scene": snap["scene"]}
        for a in result.get("actions", []):
            errs, warns = validate_action(Action(**a))
            a["validation_errors"] = errs
            a["validation_warnings"] = warns
            # The store confirmation is the one destructive prompt in the
            # product, so the slot it names has to match what the owner sees
            # in FM9-Edit. Rendered here rather than in the browser, so the
            # numbering rule stays in protocol.py alone.
            if a.get("kind") == "store" and isinstance(a.get("value"), (int, float)):
                a["slot_label"] = proto.slot_label(int(a["value"]))
            # Resolve the block to its effect id so the UI can say which other
            # scenes share its channel and will move with a parameter edit.
            # Resolved here for the same reason as the label: one place.
            if a.get("block"):
                try:
                    # resolve_block already returns (family, effect_id)
                    a["effect_id"] = reg.resolve_block(
                        a["block"], int(a.get("instance") or 1))[1]
                except Exception:
                    pass
        return result
    except Exception as e:
        return JSONResponse({"error": f"planner failed: {e}"}, status_code=502)


TEMPO_RANGE = (30, 250)   # Fractal tempo limits


def validate_action(a: Action) -> tuple[list[str], list[str]]:
    """Validate an action against the parameter reference BEFORE anything is
    sent. Returns (errors, warnings). Errors block transmission of that
    action; warnings are surfaced but do not block. Never auto-corrects.
    """
    errors: list[str] = []
    warnings: list[str] = []
    if a.kind == "set_scene":
        scene = a.value if a.value is not None else a.instance
        if not isinstance(scene, (int, float)) or not 1 <= int(scene) <= 8:
            errors.append(f"scene must be 1..8, got {scene}")
        return errors, warnings
    if a.kind == "store":
        from fm9.device import get_store_slots
        allowed = get_store_slots()
        slot = int(a.value) if a.value is not None else None
        if not allowed:
            errors.append("storing is disabled: configure TONECOMMAND_STORE_SLOTS "
                          "with slots on your unit that are safe to overwrite")
        elif slot is None or slot not in allowed:
            errors.append(f"store only allowed to configured slots "
                          f"{sorted(allowed)[0]}-{sorted(allowed)[-1]}, got {a.value}")
        else:
            warnings.append(f"store will OVERWRITE whatever is saved in slot {slot}")
        return errors, warnings
    if a.kind == "rename_preset":
        if not a.type_name or not a.type_name.strip():
            errors.append("rename_preset requires a name in type_name")
        elif len(a.type_name) > 32:
            errors.append(f"preset name exceeds 32 chars: {a.type_name!r}")
        return errors, warnings
    if a.kind == "rename_scene":
        if a.value is None or not 1 <= int(a.value) <= 8:
            errors.append(f"rename_scene requires scene 1..8 in value, got {a.value}")
        if not a.type_name or len(a.type_name) > 32:
            errors.append("rename_scene requires a name (max 32 chars) in type_name")
        return errors, warnings
    if a.kind == "set_tempo":
        if a.value is None or not TEMPO_RANGE[0] <= a.value <= TEMPO_RANGE[1]:
            errors.append(f"tempo must be {TEMPO_RANGE[0]}..{TEMPO_RANGE[1]} BPM, got {a.value}")
        return errors, warnings
    # block-addressed actions
    try:
        fam, _eid = reg.resolve_block(a.block or "", a.instance)
    except (KeyError, ValueError) as e:
        errors.append(str(e))
        return errors, warnings
    if a.kind == "add_block":
        if a.position not in (None, "pre", "post", "any"):
            errors.append(f"position must be pre/post/any, got {a.position!r}")
    elif a.kind == "bind_pedal":
        spec = reg.find_param(fam, a.param or "")
        if spec is None:
            for (f, pid), pdata in reg.params.items():
                if f == fam and pdata.get("name") == a.param:
                    spec = reg.spec(f, pid, a.instance)
                    break
        if spec is None:
            errors.append(f"unknown parameter {a.param!r} on block {a.block}")
        elif spec.kind == "enum":
            errors.append(f"{spec.name} is a selector; pedals bind to continuous parameters")
        if a.value is not None and not 0 <= a.value <= 100:
            errors.append(f"pedal floor must be 0..100 percent, got {a.value}")
    elif a.kind == "set_bypass":
        if not isinstance(a.bypassed, bool):
            errors.append("set_bypass requires bypassed true/false")
    elif a.kind == "set_channel":
        if a.value is None or int(a.value) not in (0, 1, 2, 3):
            errors.append(f"channel must be 0..3 (A-D), got {a.value}")
    elif a.kind == "set_type":
        if fam not in TYPE_PARAMS:
            errors.append(f"model selection not supported on block {a.block}")
        elif resolve_type_ordinal(fam, a.type_name or "") is None:
            errors.append(f"unknown model name: {a.type_name!r}")
    elif a.kind == "set_param":
        spec = None
        for (f, pid), pdata in reg.params.items():
            if f == fam and pdata.get("name") == a.param:
                spec = reg.spec(f, pid, a.instance)
                break
        if spec is None and a.param:
            spec = reg.find_param(fam, a.param)
        if spec is None:
            errors.append(f"unknown parameter {a.param!r} on block {a.block}")
        elif a.value is None or not isinstance(a.value, (int, float)):
            errors.append(f"{a.param} requires a numeric value, got {a.value!r}")
        elif spec.kind == "enum":
            errors.append(f"{spec.name} is a selector, not a continuous parameter; use set_type or a supported action")
        elif spec.dmin is None or spec.dmax is None:
            warnings.append(f"{spec.name} has no calibrated range in the reference; value {a.value} sent unvalidated")
        elif not spec.dmin <= a.value <= spec.dmax:
            errors.append(f"{spec.name} value {a.value} outside its range {spec.dmin}..{spec.dmax} {spec.unit or ''}")
    if a.kind == "add_block":
        warnings.append(
            "new blocks arrive with factory-default settings and will sound "
            "plain until voiced (clone a reference preset's settings or dial "
            "by ear); default voicing is not a finished sound")
    if a.kind == "bind_pedal":
        warnings.append(
            "pedal-binding curve direction is NOT verified on this hardware "
            "(issue #11): sweep may be reversed or dead; confirm by ear "
            "immediately after applying")
    return errors, warnings


def _add_block(fm9: FM9, a: Action) -> dict:
    """Insert a block onto a free shunt cell. Refuses when no sane placement
    exists rather than guessing (no cable drawing in the planner path)."""
    fam, eid = reg.resolve_block(a.block or "", a.instance)
    blocks = fm9.status_dump() or []
    if any(b.effect_id == eid for b in blocks):
        return {"ok": False, "detail": f"{a.block} {a.instance} already exists in this preset"}
    cells = fm9.read_grid() or []
    amp_cols = [c.col for c in cells if c.effect_id in (58, 59, 60, 61)]
    amp_col = min(amp_cols) if amp_cols else None
    shunts = [(c.row, c.col) for c in cells if c.is_shunt]
    pos = a.position or "any"
    if pos == "pre" and amp_col is not None:
        shunts = [(r, c) for r, c in shunts if c < amp_col]
    elif pos == "post" and amp_col is not None:
        shunts = [(r, c) for r, c in shunts if c > amp_col]
    if not shunts:
        return {"ok": False,
                "detail": f"no free pass-through cell {pos} of the amp to place "
                          f"{a.block} on; refusing rather than rewiring the grid"}
    row, col = sorted(shunts, key=lambda rc: rc[1])[0]
    fm9.place_block(row + 1, col + 1, eid)
    after = fm9.read_grid() or []
    placed = [c for c in after
              if c.effect_id == eid and (c.row, c.col) == (row, col)]
    if not placed:
        # the FM9 refuses over-budget inserts SILENTLY: nothing lands, no
        # error (hardware-observed 2026-08-21, amp2 on a loaded preset)
        still_shunt = any(c.is_shunt and (c.row, c.col) == (row, col) for c in after)
        if still_shunt:
            return {"ok": False,
                    "detail": f"insert of {a.block} landed nothing at row "
                              f"{row + 1} col {col + 1}; the FM9 refuses "
                              f"over-DSP-budget inserts silently - the preset "
                              f"is likely too heavy for this block (free up "
                              f"a block and retry)"}
    ok = bool(placed) and placed[0].cable_in_mask != 0
    # shunt-replacement can drop the OUTGOING cable (hardware-observed
    # 2026-08-21: downstream cell left with no input = silent preset).
    # Verify the next cell still has an input; redraw same-row if not.
    if ok:
        nxt = next((c for c in after if (c.row, c.col) == (row, col + 1)), None)
        if nxt is not None and nxt.cable_in_mask == 0:
            fm9.connect_cells(row + 1, col + 1, row + 1)
            after2 = fm9.read_grid() or []
            nxt2 = next((c for c in after2 if (c.row, c.col) == (row, col + 1)), None)
            if nxt2 is None or nxt2.cable_in_mask == 0:
                return {"ok": False,
                        "detail": f"placed at row {row + 1} col {col + 1} but the "
                                  f"outgoing cable was lost and could not be "
                                  f"redrawn; downstream is disconnected"}
    return {"ok": ok,
            "detail": f"placed at row {row + 1} col {col + 1}, cables verified "
                      f"in and out" if ok else "placement failed grid verification"}


def _bind_pedal(fm9: FM9, a: Action) -> dict:
    """Bind Pedal 2 to a continuous parameter using the first free modifier
    slot, with an initialized transfer curve and optional floor percent."""
    from fm9 import protocol as fp
    fam, eid = reg.resolve_block(a.block or "", a.instance)
    spec = None
    for (f, pid), pdata in reg.params.items():
        if f == fam and pdata.get("name") == a.param:
            spec = reg.spec(f, pid, a.instance)
            break
    if spec is None and a.param:
        spec = reg.find_param(fam, a.param)
    if spec is None:
        return {"ok": False, "detail": f"unknown param {a.param}"}
    slot = None
    for m in range(1, 17):
        vals = fm9.bulk_read(fp.mod_slot_eid(m))
        if vals and len(vals) > fp.MOD_PID_TARGET_EFFECT and                 vals[fp.MOD_PID_TARGET_EFFECT] == 0:
            slot = m
            break
    if slot is None:
        return {"ok": False, "detail": "no free modifier slot"}
    floor = (a.value or 0.0) / 100.0
    fm9.bind_modifier(slot, eid, spec.param_id, 11, min_norm=floor, max_norm=1.0)
    vals = fm9.bulk_read(fp.mod_slot_eid(slot))
    ok = bool(vals) and vals[fp.MOD_PID_TARGET_EFFECT] == eid and         vals[fp.MOD_PID_TARGET_PARAM] == spec.param_id and vals[fp.MOD_PID_SOURCE] == 11
    return {"ok": ok,
            "detail": f"Pedal 2 -> {spec.name} on modifier slot {slot}"
                      f"{f', floor {a.value:.0f}%' if a.value else ''}"
                      if ok else "bind failed verification"}


def run_action(fm9: FM9, a: Action) -> dict:
    if a.kind == "rename_preset":
        name = a.type_name.strip()
        if not name.upper().startswith("FM9AI"):
            name = ("FM9AI-" + name)[:32]
        fm9.rename_preset(name)
        got = fm9.current_preset()
        return {"ok": bool(got and got[1] == name), "detail": f"preset renamed to {name!r}"}
    if a.kind == "rename_scene":
        fm9.rename_scene(int(a.value), a.type_name.strip()[:32])
        got = fm9.scene_name(int(a.value))
        return {"ok": bool(got and got[1] == a.type_name.strip()[:32]),
                "detail": f"scene {int(a.value)} renamed to {a.type_name.strip()[:32]!r}"}
    if a.kind == "store":
        stored = fm9.store_preset(int(a.value))
        return {"ok": bool(stored and stored[0] == int(a.value)),
                "detail": f"stored to slot {int(a.value)}: {stored[1] if stored else '?'}"}
    if a.kind == "set_scene":
        scene_no = int(a.value) if a.value is not None else int(a.instance)
        got = fm9.set_scene(scene_no)
        name = fm9.scene_name()
        return {"ok": got == scene_no,
                "detail": f"scene {got}" + (f" \"{name[1]}\"" if name else "")}
    if a.kind == "set_tempo":
        from fm9 import protocol as p
        fm9._send(p.build_set_tempo(int(a.value)))
        return {"ok": True, "detail": f"tempo {int(a.value)} bpm sent"}

    fam, eid = reg.resolve_block(a.block or "", a.instance)
    if a.kind == "set_bypass":
        got = fm9.set_bypass(eid, bool(a.bypassed))
        return {"ok": got == bool(a.bypassed),
                "detail": "bypassed" if got else "engaged"}
    if a.kind == "set_channel":
        got = fm9.set_channel(eid, int(a.value))
        return {"ok": got == int(a.value), "detail": f"channel {'ABCD'[got]}"}
    if a.kind == "add_block":
        return _add_block(fm9, a)
    if a.kind == "bind_pedal":
        return _bind_pedal(fm9, a)
    if a.kind == "set_type":
        pid, _ = TYPE_PARAMS.get(fam, (None, None))
        if pid is None:
            return {"ok": False, "detail": f"type select not supported on {fam}"}
        resolved = resolve_type_ordinal(fam, a.type_name or "")
        if resolved is None:
            return {"ok": False, "detail": f"unknown model name: {a.type_name}"}
        ordinal, label = resolved
        spec = reg.spec(fam, pid, a.instance)
        before_wire = fm9.get_param_wire(spec)
        before = reg.amp_roster.get(str(before_wire)) if fam == "DISTORT" else before_wire
        fm9.set_param_ordinal(spec, ordinal)
        import time as _t
        ok = False
        after_label = None
        for _ in range(4):
            _t.sleep(0.15)
            wire = fm9.get_param_wire(spec)
            if wire == ordinal:
                ok = True
            roster: dict = getattr(reg, TYPE_PARAMS[fam][1])
            after_label = roster.get(str(wire), wire)
            if ok:
                break
        return {"ok": ok, "detail": f"model: {after_label}",
                "before": before, "after": after_label}
    if a.kind == "set_param":
        spec = None
        for (f, pid), pdata in reg.params.items():
            if f == fam and (pdata.get("name") == a.param):
                spec = reg.spec(f, pid, a.instance)
                break
        if spec is None and a.param:
            spec = reg.find_param(fam, a.param)
        if spec is None:
            return {"ok": False, "detail": f"unknown param {a.param} on {fam}"}
        r = fm9.set_param_display(spec, float(a.value))
        return {"ok": r.ok, "detail": r.detail,
                "before": r.display_before, "after": r.display_after}
    return {"ok": False, "detail": f"unknown action {a.kind}"}


import os as _os

GIG_SAFE_KINDS = {"set_scene"}
_gig_mode = {"on": _os.environ.get("TONECOMMAND_GIG_MODE") == "1"}


# Slot names change only when someone stores a preset, and a full sweep of
# 512 costs about 15 seconds of MIDI, so it is read once and kept. Refresh is
# explicit rather than automatic: silently re-scanning would stall a prompt.
_preset_cache: dict = {"slots": None}


@app.get("/api/presets")
def api_presets(refresh: bool = False):
    """Every slot name, read by number without disturbing the loaded preset."""
    if _preset_cache["slots"] is not None and not refresh:
        return {"slots": _preset_cache["slots"], "cached": True}
    with _lock:
        try:
            fm9 = get_fm9()
            slots = []
            for s in fm9.scan_slots(0, 511):
                slots.append({
                    "number": s.number,                       # wire
                    "editor": proto.editor_number(s.number),  # what the unit shows
                    "label": proto.slot_label(s.number),      # both, for prompts
                    "name": s.name,
                    "empty": proto.is_empty_slot_name(s.name),
                })
        except FM9NotFound:
            drop_fm9()
            return JSONResponse({"error": "FM9 not connected"}, status_code=503)
        except Exception as e:
            drop_fm9()
            return JSONResponse({"error": str(e)}, status_code=500)
    _preset_cache["slots"] = slots
    return {"slots": slots, "cached": False}


class PresetBody(BaseModel):
    number: int


@app.post("/api/preset")
def api_preset(body: PresetBody):
    """Load a preset.

    Not a planner action on purpose. Selecting a preset DISCARDS the edit
    buffer, so it is a deliberate act by a person, not something a language
    model gets to decide mid-plan. Gig mode refuses it for the same reason it
    refuses everything but a scene change.
    """
    if _gig_mode["on"]:
        return JSONResponse(
            {"error": "GIG MODE is on: refusing to change preset. Only scene "
                      "changes are allowed during a performance."},
            status_code=423)
    with _lock:
        try:
            fm9 = get_fm9()
            fm9.select_preset(body.number)
            got = fm9.current_preset()
        except FM9NotFound:
            drop_fm9()
            return JSONResponse({"error": "FM9 not connected"}, status_code=503)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
    # Report what the unit says it loaded, not what we asked for: a dropped
    # program change would otherwise look like success.
    if not got or got[0] != body.number:
        return JSONResponse(
            {"error": f"asked for {proto.slot_label(body.number)} but the unit "
                      f"reports {proto.slot_label(got[0]) if got else 'nothing'}"},
            status_code=409)
    return {"preset": {"number": got[0], "editor": proto.editor_number(got[0]),
                       "label": proto.slot_label(got[0]), "name": got[1]}}


_shared_cache: dict = {"preset": None, "map": None}


# --- undo and A/B ----------------------------------------------------------
# In memory and lost on restart, deliberately. An undo history that outlived
# the session would be offering to revert a rig it has not looked at since.
_snaps: dict = {"undo": None, "a": None, "b": None}


def _take(slot: str) -> dict:
    """Capture the edit buffer into a slot. Silent, about a quarter second."""
    snap = editbuffer.capture(get_fm9(), reg)
    _snaps[slot] = snap
    return snap


@app.get("/api/snapshots")
def api_snapshots():
    """Which slots hold something, and what undoing would actually do.

    The pending description is computed live rather than stored, because the
    buffer moves under it: a snapshot taken two edits ago describes a larger
    undo now than it did then, and a button whose label is stale about its own
    blast radius is worse than one with no label.
    """
    with _lock:
        out = {}
        # One read of the buffer for all three slots. Reading it per slot
        # tripled the MIDI traffic to answer the same question three times.
        try:
            now = editbuffer.capture(get_fm9(), reg)
            err = None
        except Exception as e:
            now, err = None, str(e)
        for slot, snap in _snaps.items():
            if snap is None:
                out[slot] = None
                continue
            row = {"preset": snap.get("preset"),
                   "preset_name": snap.get("preset_name"),
                   "scene": snap.get("scene")}
            if now is None:
                row["stale"] = True
                row["pending"] = err
            elif now.get("preset") != snap.get("preset"):
                row["stale"] = True
                row["pending"] = (f"captured on preset {snap.get('preset')}, "
                                  f"{now.get('preset')} is loaded")
            else:
                row["stale"] = False
                row["pending"] = editbuffer.summarise(
                    editbuffer.diff(reg, snap, now))
            out[slot] = row
        return out


@app.post("/api/snapshot")
def api_snapshot(body: dict):
    """Store the current edit buffer in slot a or b."""
    slot = str(body.get("slot", "")).lower()
    if slot not in ("a", "b"):
        return JSONResponse({"error": "slot must be a or b"}, status_code=400)
    with _lock:
        try:
            snap = _take(slot)
            return {"slot": slot, "preset": snap.get("preset"),
                    "blocks": len(snap.get("blocks") or [])}
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/restore")
def api_restore(body: dict):
    """Put the edit buffer back to a stored snapshot.

    Gig mode refuses. A restore writes parameters, and gig mode's whole
    position is that nothing but a scene change reaches hardware while someone
    is playing; an undo is no less a write for being a well-intentioned one.
    """
    slot = str(body.get("slot", "")).lower()
    if slot not in _snaps:
        return JSONResponse({"error": f"unknown slot {slot!r}"}, status_code=400)
    with _lock:
        if _gig_mode["on"]:
            return JSONResponse(
                {"error": "GIG MODE is on: refusing to restore. An undo writes "
                          "parameters like any other change."}, status_code=423)
        snap = _snaps.get(slot)
        if snap is None:
            return JSONResponse({"error": f"nothing captured in {slot}"},
                                status_code=409)
        try:
            fm9 = get_fm9()
            # Recalling A must not lose where you were, or A/B is a one-way
            # trip and the comparison can only be made once.
            if slot in ("a", "b"):
                other = "b" if slot == "a" else "a"
                if _snaps.get(other) is None:
                    _take(other)
            res = editbuffer.restore(fm9, reg, snap)
            return {"slot": slot, "ok": res.ok,
                    "applied": res.applied, "failed": res.failed}
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/health")
def api_health():
    """Scan the loaded preset: dead scenes, cloned scenes, level outliers.

    POST rather than GET because this is not a read. It walks the rig through
    all eight scenes to reach them, which is audible, so it must never be
    something a browser can do by prefetching a link or replaying a refresh.
    It is not cached either: a scan you did not just run is a scan describing
    a preset you may since have edited, and a stale green tick is worse than
    no tick at all.

    Gig mode refuses it. The scan makes noise and takes several seconds, which
    on stage is the definition of the thing gig mode exists to prevent.
    """
    with _lock:
        if _gig_mode["on"]:
            return JSONResponse(
                {"error": "GIG MODE is on: refusing to scan. A scan walks the "
                          "rig through every scene and is audible, which on "
                          "stage is exactly what gig mode exists to prevent."},
                status_code=423)
        try:
            return health.scan(get_fm9(), reg)
        except Exception as e:
            return {"error": str(e)}


@app.get("/api/shared")
def api_shared():
    """Which scenes share each block's channel, for the blast-radius hint.

    Cached per preset because computing it sweeps all eight scenes, which is
    audible. The UI asks for it once per preset, never on a timer.
    """
    with _lock:
        try:
            fm9 = get_fm9()
            cur = fm9.current_preset()
            key = cur[0] if cur else None
            if _shared_cache["preset"] == key and _shared_cache["map"] is not None:
                return {"preset": key, "shared": _shared_cache["map"], "cached": True}
            got = shared_scenes(fm9)
        except FM9NotFound:
            drop_fm9()
            return JSONResponse({"error": "FM9 not connected"}, status_code=503)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
    _shared_cache["preset"], _shared_cache["map"] = key, got
    return {"preset": key, "shared": got, "cached": False}


@app.post("/api/gig")
def api_gig(body: dict):
    """Performance lockout: while on, only scene changes reach hardware."""
    _gig_mode["on"] = bool(body.get("on"))
    return {"gig_mode": _gig_mode["on"]}


@app.get("/api/gig")
def api_gig_state():
    return {"gig_mode": _gig_mode["on"]}


@app.get("/api/ai-settings")
def api_ai_settings_state():
    """The saved planner choice, plus what this host can actually run.

    Never returns the API key in any form: `hasKey` says whether one is
    stored and nothing more.
    """
    return {"settings": ai_settings.panel_state(),
            "backends": ai_settings.available_backends(),
            "defaults": {"cliproxy": ai_settings.CLIPROXY_DEFAULT_URL,
                         "localLlm": ai_settings.LOCAL_LLM_DEFAULT_URL}}


@app.get("/api/ai-settings/models")
def api_ai_models(backend: str = ""):
    """Model ids to offer for a backend, and where the list came from.

    Suggestions only: every model box stays typeable, because a list that
    cannot be overridden is worse than no list once it goes stale.
    """
    return ai_settings.list_models(backend)


@app.post("/api/ai-settings")
def api_ai_settings(body: dict):
    """Save the choice and make it effective for the next prompt.

    A blank or absent apiKey keeps whatever is stored; clearKey removes it.
    """
    if not _settings_lock.acquire(timeout=2):
        return JSONResponse(
            {"error": "a plan is in flight, so nothing was saved. Try again "
                      "once it finishes: changing the backend underneath a "
                      "running plan would send it half of each setting."},
            status_code=409)
    try:
        saved = ai_settings.save(body)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    finally:
        _settings_lock.release()
    return {"settings": saved.public(),
            "backends": ai_settings.available_backends()}


@app.post("/api/apply")
def api_apply(body: ApplyBody):
    results = []
    if _gig_mode["on"]:
        blocked = [a.kind for a in body.actions if a.kind not in GIG_SAFE_KINDS]
        if blocked:
            return JSONResponse(
                {"error": f"GIG MODE is on: refusing {sorted(set(blocked))}. "
                          f"Only scene changes are allowed during a "
                          f"performance. POST /api/gig {{\"on\": false}} "
                          f"after the set."},
                status_code=423)
    with _lock:
        try:
            fm9 = get_fm9()
            if body.expected_preset is not None:
                current = fm9.current_preset()
                if current is None or current[0] != body.expected_preset:
                    return JSONResponse(
                        {"error": f"preset changed since planning (plan was for "
                                  f"{body.expected_preset}, unit is on "
                                  f"{current[0] if current else 'unknown'} "
                                  f"\"{current[1] if current else ''}\"). "
                                  f"Re-run the prompt against the current preset."},
                        status_code=409)
            # Snapshot before anything is written, so undo is always there
            # rather than something you had to remember to arm. It is silent
            # and costs about a quarter second, which is the whole reason it
            # can be automatic: reads of the loaded buffer are free, unlike
            # the scene sweep a health scan needs.
            #
            # Only for actions that actually write. A scene change is the
            # rig's own control surface and undoing it means pressing the
            # other scene, and storing is guarded by its own confirmation.
            if any(a.kind not in ("set_scene", "store") for a in body.actions):
                try:
                    _take("undo")
                except Exception as e:
                    # A snapshot that fails must not block the edit. Say so,
                    # rather than leaving an UNDO button that quietly refers
                    # to some older state than the user assumes.
                    _snaps["undo"] = None
                    results.append({"action": {"kind": "snapshot"}, "ok": False,
                                    "detail": f"could not snapshot for undo: {e}"})
            for a in body.actions:
                errs, warns = validate_action(a)
                if errs:
                    results.append({"action": a.model_dump(), "ok": False,
                                    "detail": "validation: " + "; ".join(errs)})
                    continue
                try:
                    res = run_action(fm9, a)
                except Exception as e:
                    res = {"ok": False, "detail": str(e)}
                if warns:
                    res["detail"] = (res.get("detail", "") + " | " + "; ".join(warns)).strip(" |")
                results.append({"action": a.model_dump(), **res})
                if not res.get("ok") and a.kind == "add_block":
                    # later actions in the plan target the block that failed
                    # to land; running them would set params and bind pedals
                    # on a block that is not on the grid (hardware-observed
                    # on 2026-08-20, preset 143: dangling modifier binding)
                    results.append({"action": None, "ok": False,
                                    "detail": "remaining actions skipped: "
                                              "add_block failed"})
                    break
        except FM9NotFound:
            drop_fm9()
            return JSONResponse({"error": "FM9 not connected"}, status_code=503)
    return {"results": results}


def main():
    # a choice made in the UI has to survive a restart, and the planner reads
    # its configuration from the environment, so push the saved one there
    ai_settings.apply_to_env()
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8909)


if __name__ == "__main__":
    main()
