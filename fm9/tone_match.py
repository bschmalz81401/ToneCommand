"""Reference tone-match by measurement (issue #105, G6).

Both spectra are measured the G2 way, the build's band deltas are named
against the reference ("2.4 dB more bite than the reference"), and for
each band with a real gap one amp knob and a DIRECTION are proposed from
the table in config/sound_policy.json (`reference_match`): the FM9 amp
block's own controls, a first step each, through the same plan path as
every other change. The magnitude is not computed: nobody has measured a
dB-to-knob calibration and this module will not invent one; the move is
a first step and the re-measure loop (G5) is what verifies it.

The reference's loudness is not a target (that is G3's job); the match is
spectral, so a reference at any sample rate works.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import measure as M

BLOCK = "DISTORT"
KNOB_RANGE = (0.0, 10.0)


class ToneMatchError(ValueError):
    """One line, written for the person reading the result."""


def table(pol: dict | None = None) -> dict:
    pol = pol or M.policy()
    rm = pol.get("reference_match")
    if not isinstance(rm, dict) or not isinstance(rm.get("knobs"), dict):
        raise ToneMatchError("the sound policy has no reference_match table")
    return rm


def match(build_path: str | Path, reference_path: str | Path,
          reference_label: str = "the reference", pol: dict | None = None) -> dict:
    """{reference, build, band_deltas_db, lines, moves, status}. The build
    must be a valid capture; the reference need only have a spectrum."""
    pol = pol or M.policy()
    build = M.measure(build_path, pol=pol)
    ref = M.measure(reference_path, pol=pol)
    if not build.valid:
        raise ToneMatchError(f"the build capture is invalid: {build.invalid_reason}")
    if not ref.valid:
        raise ToneMatchError(f"the reference is invalid: {ref.invalid_reason}")
    cmp = M.compare(build, ref, reference_label)
    mv = moves(cmp["band_deltas_db"], pol, reference_label)
    return {"reference": str(reference_path), "build": str(build_path),
            "band_deltas_db": cmp["band_deltas_db"], "lines": cmp["lines"], "moves": mv,
            "status": "concern" if mv else "verified",
            "coverage": {"metrics": ["bands"], "bands": list(cmp["band_deltas_db"])},
            "missing_facts": ["a dB-to-knob calibration: each move is a first step, re-measure to verify"]}


def moves(deltas: dict[str, float], pol: dict | None = None,
          reference_label: str = "the reference") -> list[dict]:
    """One knob and a direction per band whose gap is at least
    min_delta_db. `deltas` are build minus reference, in dB."""
    rm = table(pol)
    out = []
    for band, knob in rm["knobs"].items():
        d = deltas.get(band)
        if d is None or abs(d) < float(rm["min_delta_db"]):
            continue
        build_low = d < 0
        up = (knob["up_when"] == "build_low") == build_low
        out.append({"band": band, "delta_db": round(d, 2), "block": BLOCK, "param": knob["param"],
                    "pid": int(knob["pid"]), "label": knob["label"],
                    "direction": "up" if up else "down",
                    "step": float(rm["hz_step"]) if "FREQ" in knob["param"] else float(rm["step"]),
                    "why": f"{abs(d):.1f} dB {'less' if build_low else 'more'} {band.replace('_', ' ')} "
                           f"than {reference_label}: {knob['label']} {'up' if up else 'down'}"})
    return out


def actions(mv: list[dict], knobs: dict[str, float], reg: Any) -> list[dict]:
    """The moves as set_param actions on the amp block: the knob's current
    display value plus or minus its step, clamped to the registry range.
    A knob whose current value was not read gets no action, and that is
    said in the move."""
    out = []
    for m in mv:
        cur = knobs.get(m["param"])
        if cur is None:
            m["skipped"] = "the knob's current value was not read"
            continue
        try:
            spec = reg.spec(BLOCK, m["pid"], 1)
            lo, hi = float(spec.dmin), float(spec.dmax)
        except Exception:
            lo, hi = KNOB_RANGE
        want = float(cur) + (m["step"] if m["direction"] == "up" else -m["step"])
        value = max(lo, min(hi, want))
        m["from"] = float(cur)
        m["to"] = round(value, 2)
        m["clamped"] = value != want
        out.append({"kind": "set_param", "block": BLOCK, "instance": 1, "param": m["param"],
                    "value": round(value, 2),
                    "reason": f"{m['why']} ({m['label']} {cur:g} -> {value:g}, a first step; re-measure to verify)"})
    return out


def proposal(mt: dict, knobs: dict[str, float], reg: Any) -> dict:
    """The health scan's fix shape around the moves, for showPlan."""
    acts = actions(mt["moves"], knobs, reg)
    n = len(acts)
    return {"summary": (f"match the reference: {n} first step{'s' if n != 1 else ''} on the amp"
                        if n else "match the reference: within 1.5 dB in every band, nothing to move"),
            "fixes": [{"how": "actions", "label": f"{m['label'].title()} {m['direction']} ({m['why']})",
                       "reason": m["why"], "actions": [a]}
                      for m, a in zip([x for x in mt["moves"] if "skipped" not in x], acts)],
            "actions": acts,
            "skipped": [m["why"] + ": " + m["skipped"] for m in mt["moves"] if "skipped" in m]}
