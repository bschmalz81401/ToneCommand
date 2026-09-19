#!/usr/bin/env python3
"""The dedicated hardware verification procedure for #126.

    python tools/verify_headrush.py --host headrushcore.local

Its stdout IS the report: every line is scrubbed at the source (AC7), so the
transcript can be committed verbatim rather than edited into shape afterwards.

Runs the HeadRush adapter (#125) against a real unit and records what it
actually did, criterion by criterion. #126 asks for exactly this and says to run
only this, so nothing here explores: every check maps to an acceptance
criterion and the script says which.

WHAT IT WILL NOT DO

  - Touch a rig whose name does not begin with `##HRB`. Those are the test
    presets @bschmalz81401 designates; everything else on the unit is a real
    rig. The run aborts rather than continues if the loaded rig is not one.
  - Store, reset, flash or recover anything (#126 AC6). `saveRig` is not on the
    adapter's allowlist and is not called; `dirty` is asserted false at the end.
  - Write ModuleType ordinal 20. It killed the engine on this firmware
    (findings, finding 1) and the adapter refuses it; this checks the refusal
    rather than the crash.

WHAT "REFUSED BEFORE TRANSPORT" MEANS HERE (AC4)

Catching an exception proves the adapter raised. It does not prove nothing
reached the unit, which is what the criterion asks. So the client's opener is
wrapped in a counter, and the check asserts the count is UNCHANGED across the
refused call. A refusal that still opened a socket would fail.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from devices.headrush.adapter import (  # noqa: E402
    ALLOWED_METHODS, CHAIN, FOOTSWITCH, REFUSED_MODULE_ORDINALS,
    HeadrushAdapter, MethodRefused, SceneSlotState)
from devices.headrush.client import (  # noqa: E402
    HeadrushClient, describe_unreachable)
from devices.headrush.registry import load as load_registry  # noqa: E402

TEST_PREFIX = "##HRB"
RIGS = "/Evil/API/Rigs"


class Report:
    """Checks, in order, each tagged with the criterion it serves."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def record(self, ac: str, name: str, ok: bool | None, detail: str) -> None:
        self.rows.append({"ac": ac, "name": name, "ok": ok, "detail": detail})
        mark = {True: "ok  ", False: "FAIL", None: "n/a "}[ok]
        print(f"  [{mark}] {ac:<4} {name}")
        if detail:
            print(f"         {detail}")

    @property
    def failed(self) -> list[dict]:
        return [r for r in self.rows if r["ok"] is False]

    def check(self, ac: str, name: str, fn, detail: str = "") -> Any:
        """Run one check. An exception is a FAILED check, not the end of the
        run: AC5 asks for failures to be recorded rather than for capability
        claims to be quietly narrowed, and a procedure that dies on the first
        one records nothing after it."""
        try:
            ok, got = fn()
            self.record(ac, name, ok, got or detail)
            return got
        except Exception as err:                    # noqa: BLE001
            self.record(ac, name, False,
                        f"raised {type(err).__name__}: {str(err)[:120]}")
            return None


class CountingOpener:
    """Wraps the client's opener so AC4 can assert nothing left the process."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return self._inner(*args, **kwargs)


def verify(adapter: HeadrushAdapter, client: HeadrushClient,
           opener: CountingOpener, report: Report) -> None:
    # --- AC1: model and firmware ------------------------------------------
    firmware = adapter.firmware_label()
    evidence = adapter.evidence()
    report.record("AC1", "firmware string read from the unit",
                  bool(firmware), f"{evidence['model']}, AppVersion {firmware!r}")
    report.record("AC1", "matches the firmware the adapter claims evidence for",
                  firmware == evidence["firmware"],
                  f"adapter cites {evidence['firmware']}, unit says {firmware}")

    # --- AC8: the other models stay unverified ----------------------------
    report.record("AC8", "Prime and Flex Prime not claimed",
                  set(evidence["unverified_models"]) == {"Prime", "Flex Prime"},
                  "adapter lists them as unverified; nothing here tests them")

    # --- AC4: a non-allowlisted method is refused BEFORE transport --------
    before = opener.calls
    try:
        adapter.call_method(RIGS, "deleteRig", ["whatever"])
        report.record("AC4", "non-allowlisted object-method refused", False,
                      "deleteRig was NOT refused")
    except MethodRefused as err:
        report.record("AC4", "non-allowlisted object-method refused",
                      opener.calls == before,
                      f"raised MethodRefused and the opener was not called "
                      f"({before} -> {opener.calls}); {str(err)[:60]}...")
    report.record("AC4", "allowlist is deny-by-default and small",
                  ALLOWED_METHODS == frozenset({(RIGS, "loadRig")}),
                  f"only {sorted(ALLOWED_METHODS)} may be invoked")

    # --- AC2: discovery and current state ---------------------------------
    status = adapter.status_dump()
    occupied = {n: o for n, o in status["slots"].items() if o}
    report.record("AC2", "current state reads back",
                  bool(status["rig"]),
                  f"rig is a {TEST_PREFIX} preset, routing {status['routing']}, "
                  f"{len(occupied)} slots occupied")
    preset = adapter.current_preset()
    report.record("AC2", "current preset is named, not numbered",
                  preset[0] is None and bool(preset[1]),
                  f"current_preset() -> (None, <{TEST_PREFIX} name>): a name, "
                  f"not a bank/patch number")

    # --- AC2: rig listing and selection -----------------------------------
    library = client.get_properties(RIGS)
    tests = [n for n in library["AllRigNames"] if n.startswith(TEST_PREFIX)]
    report.record("AC2", "rig listing",
                  len(library["AllRigNames"]) > 0,
                  f"{len(library['AllRigNames'])} rigs, {len(tests)} are "
                  f"{TEST_PREFIX} test presets (names not recorded, AC7)")

    was = library["loadedName"]
    target = next((n for n in tests if n != was), None)
    if target is None:
        report.record("AC2", "rig selection", None,
                      "needs a second test preset to switch to; only one found")
    else:
        def select():
            adapter.select_preset(target)
            now = adapter.current_preset()[1]
            return now.strip() == target.strip(), (
                "adapter loaded the requested test preset and read it back")
        report.check("AC2", "rig selection through the adapter", select)

        # Whatever the adapter did, get the unit back where it started, by the
        # route hardware accepts. Recorded separately so a broken adapter path
        # does not leave the run unable to continue.
        ids = dict(zip(library["AllRigNames"], library["AllRigIds"]))
        client.call_method(RIGS, "loadRig", [ids[was], ""])
        time.sleep(1.5)
        report.record("AC2", "unit returned to its starting rig",
                      client.get_property(RIGS, "loadedName").strip() == was.strip(),
                      "restored directly, by rig id (name not recorded, AC7)")

    # --- the ordinal that killed the engine, refused before transport -----
    before = opener.calls
    def refuse_20():
        try:
            adapter.place_block(3, 20)
            return False, "ordinal 20 was NOT refused"
        except Exception as err:                    # noqa: BLE001
            reached = opener.calls - before
            return reached == 0, (f"{type(err).__name__} raised and the opener "
                                  f"was not called; {REFUSED_MODULE_ORDINALS[20][:58]}...")
    report.check("AC4", "ModuleType 20 refused before transport", refuse_20)

    # --- AC2 + AC3: topology selection, with read-back --------------------
    started_at = adapter.current_topology()
    def topology():
        out = adapter.select_topology(1)
        back = adapter.current_topology()
        return bool(out.get("ok")) and back == 1, (
            f"select_topology(1) -> read back {back}; adapter reports "
            f"ok={out.get('ok')}")
    report.check("AC2", "topology selection is verified by read-back", topology)
    adapter.select_topology(started_at)
    report.record("AC2", "topology restored",
                  adapter.current_topology() == started_at,
                  f"back to routing {started_at}")

    # --- AC2: representative parameter reads ------------------------------
    bass = adapter.registry.resolve("Amp", "Bass")
    def reads():
        wire = adapter.get_param_wire(bass)
        return wire is not None, f"Amp.Bass reads {wire!r} on the wire"
    report.check("AC2", "representative parameter read", reads)

    def display_refused():
        try:
            adapter.get_param_display(bass)
            return False, "a display value was returned, which is not derivable"
        except Exception as err:                    # noqa: BLE001
            return True, (f"{type(err).__name__}: the wire-to-display curve is "
                          f"taper_id={bass.taper_id!r} and the device does not "
                          f"say what that denotes, so it refuses (finding 3)")
    report.check("AC5", "display read refuses rather than inventing a value",
                 display_refused)

    # --- AC2 + AC3: a representative verified write -----------------------
    def verified_write():
        was_wire = adapter.get_param_wire(bass)
        out = adapter.set_param_wire(bass, 0.25)
        ok = bool(out.get("ok"))
        detail = out.get("detail", "")
        if was_wire is not None:
            adapter.set_param_wire(bass, was_wire)
        return ok, f"{detail[:115]}; restored to {was_wire!r}"
    report.check("AC3", "write is followed by device read-back", verified_write)

    # --- AC2: scene tri-state through the adapter -------------------------
    def scenes():
        live = adapter.scene_slots(6)
        return isinstance(live, dict), (
            f"scene 6 reads {len(live)} slot entries; "
            f"states seen: {sorted({str(v) for v in live.values()})[:3]}")
    report.check("AC2", "scene tri-state reads through the adapter", scenes)

    verify_open_questions(adapter, client, report)


def verify_open_questions(adapter: HeadrushAdapter, client: HeadrushClient,
                          report: Report) -> None:
    """The five adapter behaviours @monzta1 asked for on #33, two of which are
    open questions he wants written down either way.

    These are NOT #126 acceptance criteria and are tagged `#33` so the two do
    not get confused. They run last and in increasing order of how much they
    perturb the rig: scenes, then bypass, then chain edits. Every one restores
    what it touched, and the run's final reload discards anything that slips.
    """
    fs = client.get_properties(FOOTSWITCH) or {}
    scene_mode = [n for n in range(1, 11) if fs.get(f"ModeNew{n}") == 2]

    # --- #33 step 4: set_scene, and does SceneActive persist? -------------
    if len(scene_mode) < 2:
        report.record("#33", "set_scene", None,
                      "needs two switches in scene mode; this rig has "
                      f"{len(scene_mode)}")
    else:
        # Engage one scene, then a DIFFERENT one. Anything less cannot answer
        # the question: a flag that reads True right after its own write is
        # equally consistent with a latch and with a pulse not yet cleared,
        # and with no prior scene engaged there is no previous flag to check.
        first, second = scene_mode[0], scene_mode[1]

        def engage_first():
            out = adapter.set_scene(first)
            return bool(out.get("ok")), (
                f"set_scene(first) -> ok={out.get('ok')}, "
                f"engaged={out.get('engaged')}, "
                f"LastScene={client.get_property(FOOTSWITCH, 'LastScene')!r}")
        report.check("#33", "set_scene engages the scene", engage_first)

        after_first = client.get_properties(FOOTSWITCH) or {}

        def engage_second():
            out = adapter.set_scene(second)
            return bool(out.get("ok")), (
                f"set_scene(second) -> ok={out.get('ok')}, "
                f"engaged={out.get('engaged')}, "
                f"LastScene={client.get_property(FOOTSWITCH, 'LastScene')!r}")
        report.check("#33", "set_scene engages a second, different scene",
                     engage_second)

        # THE OPEN QUESTION: is SceneActive{n} a latch the unit maintains, or a
        # pulse it clears once the scene applies? Measured across a transition,
        # so both halves are observed: the new flag set AND the old one cleared.
        def latch():
            after = client.get_properties(FOOTSWITCH) or {}
            was_set = after_first.get(f"SceneActive{first}")
            now_new = after.get(f"SceneActive{second}")
            now_old = after.get(f"SceneActive{first}")
            ok = was_set is True and now_new is True and now_old is False
            return ok, (
                f"first scene's flag was {was_set!r} while it was active; "
                f"after engaging the second, the second reads {now_new!r} and "
                f"the first reads {now_old!r} -> "
                f"{'a LATCH the unit maintains and clears on change, not a pulse' if ok else 'NOT the latch model: see the values'}")
        report.check("#33", "SceneActive is a latch, not a pulse (OPEN)", latch)

        adapter.set_scene(first)
        report.record("#33", "scene restored",
                      adapter._current_scene() == first,
                      "back on the scene this pass started from")

    # --- #33 step 5: set_scene_slot ---------------------------------------
    if scene_mode:
        n = scene_mode[-1]
        live = adapter.scene_slots(n)
        name = next((k for k in live if k not in ("Empty Slot",)), None)
        if name is None:
            report.record("#33", "set_scene_slot", None,
                          f"scene {n} has no named slot to flip")
        else:
            was = live[name]
            want = (SceneSlotState.OFF if was is SceneSlotState.ON
                    else SceneSlotState.ON)

            def flip():
                out = adapter.set_scene_slot(n, name, want)
                back = adapter.scene_slots(n).get(name)
                return bool(out.get("ok")) and back is want, (
                    f"a slot in scene {n} moved {was.value} -> {want.value} "
                    f"and read back {back.value if back else None}")
            report.check("#33", "set_scene_slot writes both Effect and Mode",
                         flip)
            adapter.set_scene_slot(n, name, was)
            report.record("#33", "scene slot restored",
                          adapter.scene_slots(n).get(name) is was,
                          f"back to {was.value}")

    # --- #33 step 3: set_bypass -------------------------------------------
    chain = client.get_properties(CHAIN) or {}
    occupied = [i for i in range(1, 15) if int(chain.get(f"ModuleType{i}") or 0)]
    slot = next((i for i in occupied if _addressable(adapter, i)), None)
    if slot is None:
        report.record("#33", "set_bypass", None,
                      "no occupied slot resolves to an addressable block")
    else:
        block = adapter._block_for_slot(slot)
        was_on = client.get_property(block.path, "On")

        def bypass():
            # Force the block ON first, so the check observes a TRANSITION.
            # Writing False over an already-False On reads back False and
            # proves nothing.
            adapter.set_bypass(slot, False)
            lit = client.get_property(block.path, "On")
            out = adapter.set_bypass(slot, True)
            now = client.get_property(block.path, "On")
            return (lit is True and bool(out.get("ok")) and now is False), (
                f"On forced to {lit!r}, then set_bypass(slot, True) wrote "
                f"On=False and read back {now!r}")
        report.check("#33", "set_bypass writes the block's On", bypass)
        adapter.set_bypass(slot, not bool(was_on))
        report.record("#33", "bypass restored",
                      client.get_property(block.path, "On") == was_on,
                      f"On back to the value this pass found it at ({was_on!r})")

    # --- #33 step 2: place_block, including the ordinal-4 question --------
    free = next((i for i in range(1, 15)
                 if not int(chain.get(f"ModuleType{i}") or 0)), None)
    if free is None:
        report.record("#33", "place_block", None,
                      "this rig has no empty slot to place into")
        return

    def place_19():
        out = adapter.place_block(free, 19)
        return bool(out.get("ok")), f"place_block(free slot, 19) -> {out['detail']}"
    report.check("#33", "place_block places a backed ordinal", place_19)

    def empty_it():
        out = adapter.place_block(free, 0)
        return bool(out.get("ok")), f"place_block(free slot, 0) -> {out['detail']}"
    report.check("#33", "place_block empties the slot again", empty_it)

    # THE OTHER OPEN QUESTION: ordinal 4 is allowed on purpose. Finding 1 says
    # the device accepts it, then silently reverts it to 0 within ~0.4s. So the
    # adapter's delayed read-back should report NOT PLACED — a false `ok` here
    # is the failure mode the settle exists to prevent.
    def ordinal_4():
        out = adapter.place_block(free, 4)
        placed = int(client.get_property(CHAIN, f"ModuleType{free}") or 0)
        return out.get("ok") is False and placed == 0, (
            f"place_block(free slot, 4) -> ok={out.get('ok')}: "
            f"{out['detail'][:90]}; slot now holds {placed}")
    report.check("#33", "ordinal 4 is reported not placed, not falsely ok (OPEN)",
                 ordinal_4)
    adapter.place_block(free, 0)
    report.record("#33", "slot left empty",
                  int(client.get_property(CHAIN, f"ModuleType{free}") or 0) == 0,
                  "the slot this pass used is empty again")


def _addressable(adapter: HeadrushAdapter, slot: int) -> bool:
    try:
        adapter._block_for_slot(slot)
        return True
    except Exception:                               # noqa: BLE001
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", required=True)
    args = ap.parse_args(argv)

    client = HeadrushClient(args.host, args.host)
    opener = CountingOpener(client._opener)
    client._opener = opener

    try:
        loaded = client.get_property(RIGS, "loadedName")
    except Exception as err:                       # noqa: BLE001
        print(describe_unreachable(err, args.host))
        return 2

    if not str(loaded).startswith(TEST_PREFIX):
        print(f"refusing to run: the loaded rig is not a {TEST_PREFIX} test "
              f"preset. Load one on the unit first.")
        return 2

    adapter = HeadrushAdapter(client, load_registry())
    report = Report()
    print(f"verifying against {args.host}, starting on a {TEST_PREFIX} preset\n")
    verify(adapter, client, opener, report)

    # AC6 asks that nothing was stored, reset or flashed. `dirty` does not
    # answer that: it means the edit buffer differs from what is on disk, which
    # is true after any write and is discarded by a reload. What answers it is
    # that no storing method is reachable at all.
    report.record("AC6", "no storing method is on the allowlist",
                  not any(m in {"saveRig", "saveRigAs", "deleteRig",
                                "makeNewRig", "renameRig"}
                          for _, m in ALLOWED_METHODS),
                  f"allowlist is {sorted(m for _, m in ALLOWED_METHODS)}; "
                  f"store, delete, rename and create are unreachable")

    # Put the unit back. Reloading the rig by id discards the edit buffer
    # without writing anything, which is the restore-when-done rule and the
    # only route that does not involve a storing method.
    dirty_before = client.get_property(RIGS, "dirty")
    lib = client.get_properties(RIGS)
    rid = lib["AllRigIds"][lib["AllRigNames"].index(lib["loadedName"])]
    client.call_method(RIGS, "loadRig", [rid, ""])
    time.sleep(1.5)
    report.record("AC6", "edit buffer discarded at the end",
                  client.get_property(RIGS, "dirty") is False,
                  f"dirty was {dirty_before!r} after the run's writes; the rig "
                  f"was reloaded by id, which discards them without storing")

    print(f"\n{len(report.rows)} checks, {len(report.failed)} failed")
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
