#!/usr/bin/env python3
"""The hardware check for opt-in display conversion (#130's wiring, PR #168).

    python tools/verify_headrush_display.py --host headrushcore.local

WHY THIS EXISTS

Every test for the display path runs against `HeadrushSim` and REPLAYS numbers
recorded during the #130 and #167 sessions. That is real evidence about the
curve table and about the arithmetic. It is not evidence that

    HeadrushAdapter(client, registry, tapers=tapers.load())

behaves correctly against firmware, because nothing has ever constructed that
combination against a unit: `tools/verify_headrush.py` builds the adapter with
NO table, so its AC5 exercises the refusal and never the conversion.

This closes that gap and nothing else. It is the only check here that a
photograph of the screen could refute, which is the whole point: the screen is
the one source the vendor's curve table cannot have been fitted to by us.

WHAT IT WILL NOT DO

  - Touch a rig whose name does not begin with `##HRB`. Those are the test
    presets @bschmalz81401 designates; everything else on the unit is a real
    rig. The run aborts rather than continues.
  - Store, save, flash or recover anything. `saveRig` is not on the adapter's
    allowlist and is not called, so every write here lives in the edit buffer
    and dies with the next rig load.
  - Leave a value changed. Every parameter it writes is read first and
    restored in a `finally`, including when a check raises or you interrupt it.

HOW TO READ THE RESULT

Each row prints what the adapter says the unit is SHOWING. You read the same
parameter off the unit's screen and type what you see. A row passes only when
those agree; there is no automatic pass, because an automatic pass would be
the adapter checking itself against the same table it just used.

Run with `--no-prompt` to print the predictions without being asked, if you
would rather compare them against a photo afterwards.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from devices.headrush import tapers as hr_tapers        # noqa: E402
from devices.headrush.adapter import (                  # noqa: E402
    DerivedDisplay, HeadrushAdapter)
from devices.headrush.client import (                   # noqa: E402
    HeadrushClient, describe_unreachable)
from devices.headrush.registry import (                 # noqa: E402
    NotMeasured, load as load_registry)
from tools.verify_headrush import (                     # noqa: E402
    RIGS, TEST_PREFIX, redact, teach_redactor)

TAPER_TABLE = ROOT / "config" / "headrush_tapers.json"

#: What the unit CALLS these, for the human being asked to read one.
#:
#: Hand-made, and it has to be: `config/headrush_registry.json` publishes the
#: property name and no label, so nothing in this repo can turn `PostGain`
#: into "Output Level". Asking someone to find "Amp.PostGain" on a screen that
#: says "Output Level" is how the first run of this script sent @bschmalz81401
#: to the High Volume control instead, and a mis-read control would have been
#: recorded as a failed conversion.
#:
#: Read off the editor on 2026-09-20, covering only the parameters this script
#: prompts for. A parameter absent from here is prompted by its property name
#: alone rather than by a guess.
SCREEN_LABELS = {
    "Amp.Bass": "Bass",
    "Amp.Treble": "Treble",
    "Amp.PostGain": "Output Level",
    "Amp.TremDepth": "Depth, in the tremolo section",
    "Amp.TremSpeed": "Speed, in the tremolo section",
}


class Rows:
    """The transcript. Redacted at print time, like the #126 harness."""

    def __init__(self) -> None:
        self.passed = self.failed = self.skipped = 0

    def record(self, name: str, ok: bool | None, detail: str) -> None:
        mark = {True: "PASS", False: "FAIL", None: "SKIP"}[ok]
        if ok is True:
            self.passed += 1
        elif ok is False:
            self.failed += 1
        else:
            self.skipped += 1
        print(redact(f"[{mark}] {name}\n       {detail}"))

    def summary(self) -> int:
        print(f"\n{self.passed} passed, {self.failed} failed, "
              f"{self.skipped} skipped")
        return 1 if self.failed else 0


def ask(prompt: str, no_prompt: bool) -> str | None:
    """What the operator reads off the unit, or None when not asked."""
    if no_prompt:
        return None
    try:
        return input(redact(prompt)).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None


def check_the_screen(adapter, rows, spec, wire, no_prompt) -> None:
    """Write a known wire value, then compare the conversion with the screen.

    The wire value is the one the recorded reading was taken at, so the screen
    should read what `config/headrush_tapers.json` says it did. This is the
    only comparison in the suite that the unit itself can contradict.
    """
    where = f"{spec.block}.{spec.name}"
    out = adapter.set_param_wire(spec, wire)
    shown = adapter.get_param_display(spec)
    assert isinstance(shown, DerivedDisplay), "a bare value came back"

    detail = (f"wire {wire!r} written (ok={out.get('ok')!r}); the adapter "
              f"says the screen reads {shown.text!r} "
              f"[curve {shown.curve}, {shown.provenance}, "
              f"api_readable={shown.api_readable}]")
    label = SCREEN_LABELS.get(where)
    called = f'"{label}"' if label else f"{where} (no label recorded)"
    seen = ask(f"       look at {called} on the unit. What does it show? "
               f"(blank to skip) ", no_prompt)
    if seen is None or seen == "":
        rows.record(f"{where} at wire {wire!r}", None,
                    detail + " - not compared against the screen")
        return
    agree = seen.replace(" ", "").lower() == shown.text.replace(" ", "").lower()
    rows.record(f"{where} at wire {wire!r}", agree,
                detail + f"; you read {seen!r}")


def check_the_write_path(adapter, rows, spec, display_value) -> None:
    """Ask for a display value, then read back what the unit actually holds.

    `ok` is expected to be FALSE on a quantized parameter and that is #167,
    not a failure of this conversion: `_write_verified` compares the read-back
    with exact equality, and the unit snaps the DISPLAY value to its published
    grid and converts back. The row passes when the value the unit holds is
    the grid neighbour of what was asked for; it fails if the unit is holding
    something the curve cannot explain.
    """
    where = f"{spec.block}.{spec.name}"
    out = adapter.set_param_display(spec, display_value)
    held = adapter.get_param_display(spec)
    grid = (spec.published or {}).get("grid")

    explained = grid is not None and abs(held.value - display_value) <= grid
    rows.record(
        f"{where}: set_param_display({display_value!r})",
        explained,
        f"asked for {display_value!r}, the unit is showing {held.text!r} "
        f"(grid {grid:g}); write reported ok={out.get('ok')!r}"
        + ("  <- #167: exact-equality read-back on a quantized parameter, "
           "expected here and not a failure of the conversion"
           if out.get("ok") is False else ""))


def shows_the_same(adapter, spec, a, b) -> bool:
    """Whether two wire values render as the same text on the unit.

    Used only to tell a quantized read-back apart from a restore that really
    did not land. Conversion failing for either value answers False, because
    "I could not tell" must not read as "they agree".
    """
    try:
        texts = set()
        for wire in (a, b):
            value = adapter._converted(spec, "to_display", float(wire))
            texts.add(adapter._formatted(spec, value))
        return len(texts) == 1
    except Exception:                                   # noqa: BLE001
        return False


def check_the_default_still_refuses(client, registry, rows) -> None:
    """The opt-in promise, against real firmware rather than the simulator.

    Every existing caller builds the adapter with no table, and none of them
    should start receiving converted numbers because this landed.
    """
    plain = HeadrushAdapter(client, registry)
    spec = registry.resolve("Amp", "Bass")
    try:
        plain.get_param_display(spec)
    except NotMeasured as err:
        rows.record("an adapter built with no table still refuses", True,
                    redact(str(err))[:150])
    else:
        rows.record("an adapter built with no table still refuses", False,
                    "it returned a display value, which no existing caller "
                    "should now receive")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", required=True)
    ap.add_argument("--no-prompt", action="store_true",
                    help="print the predictions without asking what you see")
    args = ap.parse_args(argv)

    client = HeadrushClient(args.host, args.host)
    try:
        loaded = client.get_property(RIGS, "loadedName")
        library = client.get_properties(RIGS)
    except Exception as err:                            # noqa: BLE001
        print(describe_unreachable(err, args.host))
        return 2

    if not str(loaded).startswith(TEST_PREFIX):
        print(f"refusing to run: the loaded rig is not a {TEST_PREFIX} test "
              f"preset. Load one on the unit first.")
        return 2

    teach_redactor(args.host, list(library.get("AllRigNames") or []),
                   list(library.get("AllRigIds") or []))

    registry = load_registry()
    adapter = HeadrushAdapter(client, registry, tapers=hr_tapers.load())
    rows = Rows()
    print(redact(f"display conversion against {args.host}, on a "
                 f"{TEST_PREFIX} preset\n"))

    recorded = json.loads(TAPER_TABLE.read_text())["hardware_check"]
    specs, originals = {}, {}
    try:
        # Read everything this run will touch BEFORE writing any of it, so the
        # restore below is against what the rig actually held.
        for row in recorded:
            block, name = row["property"].split(".")
            spec = specs.setdefault(row["property"], registry.resolve(block, name))
            if row["property"] not in originals:
                originals[row["property"]] = adapter.get_param_wire(spec)

        for row in recorded:
            check_the_screen(adapter, rows, specs[row["property"]],
                             row["wire"], args.no_prompt)

        trem = registry.resolve("Amp", "TremSpeed")
        check_the_write_path(adapter, rows, trem, 5.19)
        check_the_default_still_refuses(client, registry, rows)
    finally:
        # Put back the LITERAL value each property reported, through the
        # client rather than through set_param_wire. The typed write validates
        # against the published wire range and refuses anything outside it, so
        # a unit holding a value that range does not describe could be read,
        # overwritten, and then not restored - the one failure this script
        # must not have. Verified by reading back, since bypassing the typed
        # path also bypasses its read-back check.
        restored = unrestored = 0
        for where, was in originals.items():
            if was is None:
                continue
            spec = specs[where]
            try:
                client.set_properties(registry.block(spec.block).path,
                                      {spec.name: was})
                back = adapter.get_param_wire(spec)
                if back == was:
                    restored += 1
                elif shows_the_same(adapter, spec, back, was):
                    # NOT a failed restore. The unit converts a written wire
                    # value to display, snaps the display to its grid and
                    # converts back (#167), so on a quantized parameter there
                    # is NO write that returns the stored float to what it was
                    # - `0.5` on Amp.TremSpeed comes back `0.5001265...` every
                    # time. The first version of this called that unrestored
                    # and told the operator to reload the rig, which made the
                    # device working as designed look like this script having
                    # left the rig dirty.
                    restored += 1
                    print(redact(
                        f"[note] {where} reads back {back!r} rather than the "
                        f"{was!r} it held: the unit quantizes this parameter "
                        f"and shows the same value either way. Restored as "
                        f"far as the unit can be."))
                else:
                    unrestored += 1
                    print(redact(f"[WARN] {where} restored to {back!r}, not "
                                 f"the {was!r} it held before this run, and "
                                 f"it is NOT showing the same value"))
            except Exception as err:                    # noqa: BLE001
                unrestored += 1
                print(redact(f"[WARN] could not restore {where} to {was!r}: "
                             f"{err}"))
        if originals:
            print(f"\nrestored {restored} parameter(s) to the value they held "
                  f"before this run"
                  + (f"; {unrestored} NOT restored - reload the rig on the "
                     f"unit to discard the edit buffer" if unrestored else ""))

    return rows.summary()


if __name__ == "__main__":
    raise SystemExit(main())
