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
    adapter's allowlist and is not called.
  - Name a real destructive method, or write ModuleType ordinal 20, as part of
    checking that they are refused. See below.

A SAFETY CHECK MUST NOT BE THE THING IT CHECKS FOR (review of #134)

The first version probed AC4 by calling `deleteRig` and by writing ordinal 20,
the ordinal that killed the engine. Both are safe ONLY IF the interlock works,
which is the thing under test: a broken allowlist would have deleted a rig, and
a broken refusal table would have written the crash ordinal to a hardcoded slot
that is occupied on a real rig. The probe is now split so a failure of the
mechanism cannot execute the dangerous operation:

  - the dangerous NAMES are checked as data, with no call at all: `deleteRig`
    and friends are asserted absent from the allowlist, and 20 is asserted
    present in the refusal table.
  - the TRANSPORT behaviour is checked with operands that are harmless even if
    every guard fails: a method name no device implements, and ordinal 254,
    which finding 1 measured as sticking without an object rather than
    crashing. It is written to a slot measured empty on this rig, never a
    hardcoded one.

WHAT "REFUSED BEFORE TRANSPORT" MEANS HERE (AC4)

Catching an exception proves the adapter raised. It does not prove nothing
reached the unit, which is what the criterion asks. So the client's opener is
wrapped in a counter, and the check asserts the count is UNCHANGED across the
refused call. A refusal that still opened a socket would fail.

That counter is only a complete answer if every byte leaves through that one
opener. It does: `HeadrushAdapter` performs no I/O of its own and reaches the
network only through its client, and `HeadrushClient` has exactly one outbound
call site, `self._opener(...)`. The run asserts the adapter is holding the
client that was wrapped.

AC7 IS ENFORCED AT PRINT TIME

`Report.record` runs every line through `redact()` before printing, so the
host and rig names cannot reach the transcript even from an exception message
or a detail string built elsewhere. The committed report is this output
verbatim; nothing is edited into shape afterwards.
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
from devices.headrush.registry import (  # noqa: E402
    NotMeasured, load as load_registry)

TEST_PREFIX = "##HRB"
RIGS = "/Evil/API/Rigs"

# How still the chain has to be before a rig counts as loaded. Three identical
# observations is two intervals, so half a second of quiet.
QUIET_SAMPLES = 3
QUIET_INTERVAL_S = 0.25

# Quiet alone is NOT enough, because the PREVIOUS rig's chain is also quiet:
# it was measured sitting unchanged for about a second after the name flipped.
# So the chain must also have stopped being the one from before the load. When
# it legitimately never differs - reloading the same rig, or two rigs with the
# same chain - that can never be observed, and this is the ceiling to wait out
# instead. The longest rebuild measured was 1392 ms after loadRig.
REBUILD_CEILING_S = 2.5

# AC7. Filled in once the host and library are known, then applied to EVERY
# printed line. Redacting at the call sites was the earlier design and it
# leaked: an exception message carries the url, so the host reached the
# transcript and was edited out by hand afterwards, while the report claimed
# print-time redaction. Doing it here makes the claim true.
_SECRETS: list[tuple[str, str]] = []


def teach_redactor(host: str, rig_names: list[str],
                   rig_ids: list[str] | None = None) -> None:
    """Seed everything AC7 promises is absent from the transcript.

    Names alone were not enough: the report also promises no rig id, and an
    error body or payload can carry one (review of #136). Longest first, so a
    name that contains another is masked whole.
    """
    _SECRETS.clear()
    for rid in sorted((r for r in (rig_ids or []) if r), key=len, reverse=True):
        _SECRETS.append((str(rid), "<rig id>"))
    for name in sorted((n for n in rig_names if n), key=len, reverse=True):
        _SECRETS.append((name, f"<{TEST_PREFIX} name>"
                               if name.startswith(TEST_PREFIX) else "<rig>"))
    _SECRETS.append((host, "<host>"))


def redact(text: str) -> str:
    out = str(text)
    for secret, mask in _SECRETS:
        out = out.replace(secret, mask)
        out = out.replace(secret.strip(), mask)
    return out


def chain_shape(client: HeadrushClient):
    """What the chain looks like right now: routing plus every slot's module.

    The comparison value for "has the rig actually changed underneath us".
    """
    chain = client.get_properties(CHAIN) or {}
    return (chain.get("Routing"),
            tuple(chain.get(f"ModuleType{n}") for n in range(1, 15)))


def wait_for_rig(client: HeadrushClient, name: str, timeout_s: float = 12.0,
                 before=None):
    """Wait until `name` is loaded AND the engine has finished building it.

    Three things, because the unit gives no single signal for "loaded".

    `loadedName` flips early. Measured on a Core at 5.1.0.2a63755, it flips
    185..332 ms after loadRig while the chain is STILL THE PREVIOUS RIG'S, and
    the chain is replaced up to a second later:

        trial 1: name flipped at t+332 ms, chain still 9 modules (the old rig)
                 became 4 modules at t+1392 ms
        trial 3: name flipped at t+185 ms, chain changed at t+1140 ms

    A write issued when the name says loaded therefore races the tail of the
    load and loses: the #126 topology check wrote Routing, read back the value
    the load then installed, and reported a mismatch that was not the
    adapter's fault.

    Quiescence alone does not fix that, which is the trap this fell into first
    (review of #136). The PREVIOUS rig's chain is quiet too, and it was
    measured quiet for longer than any reasonable quiet window, so waiting for
    stillness can succeed on the old chain and return just as early. The chain
    must also have STOPPED BEING the one from before the load.

    `before` is that pre-load shape, from `chain_shape()`. Two rigs can share
    a chain, and reloading a rig certainly does, so a shape that never differs
    is not an error: `REBUILD_CEILING_S` past the name flip is waited out
    instead. Returns the loaded name, or None if any wait timed out.
    """
    deadline = time.monotonic() + timeout_s
    loaded = None
    while time.monotonic() < deadline:
        try:
            now = str(client.get_property(RIGS, "loadedName") or "")
        except Exception:                           # noqa: BLE001
            time.sleep(0.05)
            continue
        if now.strip() == name.strip():
            loaded = now
            break
        time.sleep(0.05)
    if loaded is None:
        return None

    named_at = time.monotonic()
    changed = before is None
    stable = 0
    previous = None
    while time.monotonic() < deadline:
        try:
            shape = chain_shape(client)
        except Exception:                           # noqa: BLE001
            time.sleep(QUIET_INTERVAL_S)
            continue
        if before is not None and shape != before:
            changed = True
        stable = stable + 1 if shape == previous else 1
        previous = shape
        waited_out = time.monotonic() - named_at >= REBUILD_CEILING_S
        if stable >= QUIET_SAMPLES and (changed or waited_out):
            return loaded
        time.sleep(QUIET_INTERVAL_S)
    return None


class Report:
    """Checks, in order, each tagged with the criterion it serves."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def record(self, ac: str, name: str, ok: bool | None, detail: str) -> None:
        detail = redact(detail)
        self.rows.append({"ac": ac, "name": name, "ok": ok, "detail": detail})
        mark = {True: "ok  ", False: "FAIL", None: "n/a "}[ok]
        print(f"  [{mark}] {ac:<4} {redact(name)}")
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

    def expect_raise(self, ac: str, name: str, want: type | tuple,
                     fn, detail_ok, detail_no: str) -> None:
        """A refusal check. The exception type is REQUIRED to be the one the
        refusal is supposed to raise: treating any Exception as a pass greens
        the row on a transport error or a TypeError, which is the opposite of
        what the criterion asks.

        `detail_ok` is a CALLABLE, deliberately. Passing a formatted string
        evaluates it before `fn` runs, so a message quoting an opener count
        reports the count from before the call and reads the same whether or
        not anything went out. That is the uninformative-message bug this
        procedure fixed elsewhere, reintroduced by an argument's evaluation
        order (review of #136)."""
        try:
            fn()
        except want as err:                         # the refusal, as designed
            got = detail_ok() if callable(detail_ok) else detail_ok
            self.record(ac, name, True, f"{type(err).__name__}: {got}")
        except Exception as err:                    # noqa: BLE001
            self.record(ac, name, False,
                        f"raised {type(err).__name__}, not "
                        f"{getattr(want, '__name__', want)}: {str(err)[:90]}")
        else:
            self.record(ac, name, False, detail_no)


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

    # --- AC4: the dangerous NAMES, checked as data, with no call ----------
    # Nothing here invokes anything. A broken allowlist cannot delete a rig by
    # way of the test that checks the allowlist.
    destructive = {"deleteRig", "saveRig", "saveRigAs", "makeNewRig",
                   "renameRig", "factoryReset", "updateFirmware"}
    on_list = {m for _, m in ALLOWED_METHODS}
    report.record("AC4", "no destructive method is on the allowlist",
                  not (on_list & destructive),
                  f"allowlist is {sorted(on_list)}; none of "
                  f"{sorted(destructive)} appears")
    report.record("AC4", "allowlist is deny-by-default and small",
                  ALLOWED_METHODS == frozenset({(RIGS, "loadRig")}),
                  f"only {sorted(ALLOWED_METHODS)} may be invoked")
    report.record("AC4", "ordinal 20 is in the refusal table",
                  20 in REFUSED_MODULE_ORDINALS,
                  "the engine-killing ordinal is refused by data, checked "
                  "without writing it")

    # --- AC4: the transport behaviour, with a harmless operand ------------
    # A method name no device implements. If the allowlist were broken this
    # would 404, not destroy anything.
    before = opener.calls
    report.expect_raise(
        "AC4", "non-allowlisted object-method refused before transport",
        MethodRefused,
        lambda: adapter.call_method(RIGS, "toneCommandNoSuchMethod", []),
        detail_ok=lambda: (f"refused; opener went {before} -> {opener.calls}"),
        detail_no="a method outside the allowlist was NOT refused")
    report.record("AC4", "nothing reached the unit during that refusal",
                  opener.calls == before,
                  f"opener call count unchanged ({before} -> {opener.calls})")

    # The counter only answers AC4 if the adapter cannot reach the network
    # another way. It holds the wrapped client, and the client has one
    # outbound call site.
    # This proves the wrapping is in place, which is all an assertion can do
    # from here. That the wrapped opener is the ONLY way out is a property of
    # the code, not of this run: HeadrushAdapter performs no I/O of its own,
    # and HeadrushClient has exactly one outbound call site, `self._opener`.
    # Stated in the docstring rather than dressed up as a measurement.
    report.record("AC4", "the adapter is holding the wrapped client",
                  adapter.client is client and client._opener is opener,
                  "the counter is in the path the adapter actually uses")

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
            was_chain = chain_shape(client)      # before the load, or the
            out = adapter.select_preset(target)  # "has it changed" test is inert
            # loadRig returns before the engine swaps. Measured on this unit,
            # the swap lands 159..679 ms after the call, so a fixed wait is
            # either a flake or slower than it needs to be; poll instead.
            now = wait_for_rig(client, target, before=was_chain)
            ok = now is not None
            return ok, (f"adapter loaded the requested test preset and it read "
                        f"back after {'polling' if ok else 'a timeout'}; "
                        f"adapter itself reported ok={out.get('ok')!r}")
        report.check("AC2", "rig selection through the adapter", select)

        # Whatever the adapter did, get the unit back where it started, by the
        # route hardware accepts. Recorded separately so a broken adapter path
        # does not leave the run unable to continue.
        ids = dict(zip(library["AllRigNames"], library["AllRigIds"]))
        was_chain = chain_shape(client)
        client.call_method(RIGS, "loadRig", [ids[was], ""])
        report.record("AC2", "unit returned to its starting rig",
                      wait_for_rig(client, was, before=was_chain) is not None,
                      "restored directly, by rig id (name not recorded, AC7)")

    # --- AC4: the refusal blocks before transport, using a safe ordinal ---
    # 254 is also in the refusal table, and finding 1 measured it as sticking
    # without an object rather than crashing. So if the refusal failed, the
    # worst case is a harmless ordinal in a slot this run already measured
    # EMPTY, not the crash ordinal in a hardcoded, occupied one.
    chain_now = client.get_properties(CHAIN) or {}
    spare = next((i for i in range(1, 15)
                  if not int(chain_now.get(f"ModuleType{i}") or 0)), None)
    if spare is None:
        report.record("AC4", "ModuleType refusal blocks before transport", None,
                      "needs an empty slot so a failed refusal stays harmless")
    else:
        before = opener.calls
        report.expect_raise(
            "AC4", "refused ModuleType blocks before transport",
            PermissionError,
            lambda: adapter.place_block(spare, 254),
            detail_ok=lambda: (f"ordinal 254 refused; opener went {before} -> "
                               f"{opener.calls}. 20 is refused by the same "
                               f"table, asserted above without writing it"),
            detail_no="a refused ordinal was NOT refused")
        report.record("AC4", "nothing reached the unit during that refusal",
                      opener.calls == before,
                      f"opener call count unchanged ({before} -> {opener.calls})")

    # --- AC2 + AC3: topology selection, with read-back --------------------
    started_at = adapter.current_topology()
    other = 1 if started_at != 1 else 0          # always a real transition
    def topology():
        out = adapter.select_topology(other)
        back = adapter.current_topology()
        return (bool(out.get("ok")) and back == other and back != started_at), (
            f"routing moved {started_at} -> {back} and read back; adapter "
            f"reports ok={out.get('ok')}")
    report.check("AC2", "topology selection is verified by read-back", topology)
    restore = adapter.select_topology(started_at)
    back = adapter.current_topology()
    report.record("AC2", "topology restored", back == started_at,
                  f"wanted routing {started_at}, unit reads {back!r}; the "
                  f"restoring write reported ok={restore.get('ok')!r} "
                  f"({restore.get('detail', '')[:70]})")

    # --- AC2: representative parameter reads ------------------------------
    bass = adapter.registry.resolve("Amp", "Bass")
    def reads():
        wire = adapter.get_param_wire(bass)
        return wire is not None, f"Amp.Bass reads {wire!r} on the wire"
    report.check("AC2", "representative parameter read", reads)

    report.expect_raise(
        "AC5", "display read refuses rather than inventing a value",
        NotMeasured,
        lambda: adapter.get_param_display(bass),
        detail_ok=lambda: (f"the wire-to-display curve is "
                           f"taper_id={bass.taper_id!r} and the device does "
                           f"not say what that denotes, so it refuses "
                           f"(finding 3)"),
        detail_no="a display value was returned, which is not derivable")

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
    # Snapshot at entry. Restoring to scene_mode[0] was wrong: the run's own
    # mid-pass reload clears LastScene, so "back on the scene this pass
    # started from" was a claim the code did not implement.
    scene_at_entry = adapter._current_scene()

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

        if scene_at_entry is None:
            report.record("#33", "scene restored", None,
                          "no scene was engaged when this pass started, so "
                          "there is nothing to restore to; the final reload "
                          "settles it")
        else:
            adapter.set_scene(scene_at_entry)
            report.record("#33", "scene restored",
                          adapter._current_scene() == scene_at_entry,
                          "back on the scene this pass found engaged at entry")

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
        return bool(out.get("ok")), f"place_block(free slot, 19) -> {out.get('detail', '')}"
    report.check("#33", "place_block places a backed ordinal", place_19)

    def empty_it():
        out = adapter.place_block(free, 0)
        return bool(out.get("ok")), f"place_block(free slot, 0) -> {out.get('detail', '')}"
    report.check("#33", "place_block empties the slot again", empty_it)

    # THE OTHER OPEN QUESTION: ordinal 4 is allowed on purpose. Finding 1 says
    # the device accepts it, then silently reverts it to 0 within ~0.4s. So the
    # adapter's delayed read-back should report NOT PLACED - a false `ok` here
    # is the failure mode the settle exists to prevent.
    def ordinal_4():
        out = adapter.place_block(free, 4)
        placed = int(client.get_property(CHAIN, f"ModuleType{free}") or 0)
        return out.get("ok") is False and placed == 0, (
            f"place_block(free slot, 4) -> ok={out.get('ok')}: "
            f"{out.get('detail', '')[:90]}; slot now holds {placed}")
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


def discard_edits(client: HeadrushClient) -> tuple[Any, Any]:
    """Reload the loaded rig by id, which drops the edit buffer without
    writing anything. The only restore route that does not go through a
    storing method.

    Raises if the rig did not come back. Returning quietly let the caller
    green "edit buffer discarded" off a timeout (review of #136).
    """
    before = client.get_property(RIGS, "dirty")
    lib = client.get_properties(RIGS)
    name = lib["loadedName"]
    rid = lib["AllRigIds"][lib["AllRigNames"].index(name)]
    was_chain = chain_shape(client)
    client.call_method(RIGS, "loadRig", [rid, ""])
    if wait_for_rig(client, name, before=was_chain) is None:
        raise TimeoutError("the rig did not reload within the timeout, so the "
                           "edit buffer was NOT discarded")
    return before, client.get_property(RIGS, "dirty")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", required=True)
    args = ap.parse_args(argv)

    client = HeadrushClient(args.host, args.host)
    opener = CountingOpener(client._opener)
    client._opener = opener

    try:
        loaded = client.get_property(RIGS, "loadedName")
        library = client.get_properties(RIGS)
    except Exception as err:                       # noqa: BLE001
        print(describe_unreachable(err, args.host))
        return 2

    if not str(loaded).startswith(TEST_PREFIX):
        print(f"refusing to run: the loaded rig is not a {TEST_PREFIX} test "
              f"preset. Load one on the unit first.")
        return 2

    # AC7, before anything is printed: from here on no line can carry the host
    # or a rig name, including one that arrives inside an exception message.
    teach_redactor(args.host, list(library.get("AllRigNames") or []),
                   list(library.get("AllRigIds") or []))

    adapter = HeadrushAdapter(client, load_registry())
    report = Report()
    print(redact(f"verifying against {args.host}, starting on a "
                 f"{TEST_PREFIX} preset\n"))

    # Every device write below has to be undone even if a check explodes.
    # `verify` was previously called bare: any exception it did not convert to
    # a FAILED row skipped the restore and left a dirty edit buffer, possibly
    # with chain edits in it.
    crashed = interrupted = None
    try:
        verify(adapter, client, opener, report)
    except Exception as err:                        # noqa: BLE001
        crashed = err
    except (KeyboardInterrupt, SystemExit) as err:
        # Still restore, then let it through. Swallowing these and returning 1
        # turns Ctrl-C into an ordinary failing run (review of #136).
        interrupted = err
    finally:
        # AC6 asks that nothing was stored, reset or flashed. `dirty` does not
        # answer that: it is true after any write and false after a reload.
        # What answers it is that no storing method is reachable at all.
        report.record("AC6", "no storing method is on the allowlist",
                      not any(m in {"saveRig", "saveRigAs", "deleteRig",
                                    "makeNewRig", "renameRig"}
                              for _, m in ALLOWED_METHODS),
                      f"allowlist is {sorted(m for _, m in ALLOWED_METHODS)}; "
                      f"store, delete, rename and create are unreachable")
        try:
            dirty_before, dirty_after = discard_edits(client)
            # `not dirty_after` greened this row on None, which is what a
            # failed read returns: an unreadable unit reported a clean one
            # (review of #136). Only an explicit false reading counts.
            report.record("AC6", "edit buffer discarded at the end",
                          dirty_after is False or dirty_after == 0,
                          f"dirty was {dirty_before!r} after the run's writes "
                          f"and reads {dirty_after!r} now; the rig was "
                          f"reloaded by id, which discards them without "
                          f"storing")
        except Exception as err:                    # noqa: BLE001
            report.record("AC6", "edit buffer discarded at the end", False,
                          f"THE RESTORE ITSELF FAILED ({type(err).__name__}): "
                          f"the unit may be holding this run's edits. Reload "
                          f"the rig on the unit; nothing was stored, so a "
                          f"reload is sufficient.")

    if crashed is not None:
        report.record("--", "the run did not finish", False,
                      f"{type(crashed).__name__}: {str(crashed)[:110]}; the "
                      f"restore above still ran")
    if interrupted is not None:
        restored = report.rows[-1]["ok"] if report.rows else None
        print("\ninterrupted; the unit was restored before exiting"
              if restored else
              "\ninterrupted; THE RESTORE WAS ATTEMPTED AND DID NOT CONFIRM. "
              "Reload the rig on the unit; nothing was stored.")
        raise interrupted

    print(f"\n{len(report.rows)} checks, {len(report.failed)} failed")
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
