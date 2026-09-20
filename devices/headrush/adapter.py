"""The HeadRush as a DeviceAdapter (#125, phase 4 of #33).

Built ON the committed pieces, not beside them: `HeadrushClient` (the
transport, with an injectable opener), `devices.headrush.registry` (the
schema-backed catalog) and `devices.headrush.topology` (the ten routings).
Every test runs the real client against `HeadrushSim`; no hardware here.

WHAT IT CLAIMS, AND ON WHAT EVIDENCE

Every capability flag rests on docs/HEADRUSH-HARDWARE-FINDINGS.md (HeadRush
Core, firmware 5.1.0.2a63755, measured 2026-09-15) or on the #109 chain
measurement, and each False below is an absence that was measured or not
measured, never a guess dressed as a fact:

  read_path DEVICE            the unit answers on the channel it is written
                              on (HTTP both ways)
  observes_foreign_writes     a websocket reports changes this tool did not
                              make (#33)
  reads_slot_names True       /Evil/API/Rigs publishes RigNames without
                              loading anything
  reads_slot_state False      a rig's chain cannot be read until it is
                              loaded (Capabilities docstring, #33)
  verifies_writes True        every write here is read back after a settle,
                              and a placement also checks the object exists
                              (finding 1: read-back alone misses two cases)
  has_scenes True             finding 4; composable_scene_slots True because
                              a scene stores NO_CHANGE per slot, by name
  stores_presets False        no store operation has been measured; nothing
                              here saves a rig
  topology SELECTED           ten routings, one integer, shapes not published
                              (#109); has_modifiers, installs_files and
                              can_rename False: not measured, so not claimed

WHAT IT REFUSES BEFORE TRANSPORT

  - object-method outside ALLOWED_METHODS (only loadRig, measured in
    finding 4). Firmware, recovery, update, reset and every unreviewed
    method are unreachable by construction: they are not in the set.
  - Chain.ModuleType ordinal 20: measured engine death (finding 1), and no
    read-back catches it. Ordinal 254: sticks with no object to address.
    Ordinal 4 is NOT refused: the unit rejects it itself in ~0.4 s, which the
    delayed read-back reports honestly as "not placed".

WHAT IT DOES NOT PRETEND

  Continuous parameters take 0..1 on the wire and the device names each
  curve only by an opaque taper id (finding 3). BY DEFAULT both display
  methods still refuse with the registry's own reason and the wire value is
  offered instead. Passing `tapers=devices.headrush.tapers.load()` opts in to
  the curves read out of the VENDOR'S EDITOR, which is a different source
  and not a claim about the API: a converted read comes back as a
  DerivedDisplay carrying `api_readable=False` and where the maths came
  from, never as a bare number. Channels, preset slots, file installs and
  modifiers have no HeadRush meaning and raise NotSupported.

  The `ok` flag on any write is EXACT WIRE EQUALITY, and on a parameter the
  unit snaps to its display grid it reports False for a write the unit
  honoured (#167). That is true of every write path here and predates the
  display methods; it is not fixed by converting the units.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from fm9.adapter import Capabilities, ReadPath, SceneSlotState, Topology
from devices.headrush import topology as topo
from devices.headrush.registry import NotMeasured, Registry, UnknownBlock
from devices.headrush.tapers import TaperTable

CHAIN = "/Evil/Engine/Patch/Chain"
RIG = "/Evil/Engine/Patch/Rig"
RIGS = "/Evil/API/Rigs"
FOOTSWITCH = "/Evil/Engine/FootSwitch"
GUI = "/Evil/Gui"
SLOTS = topo.SLOTS
SCENES = 10

#: (path, method) pairs this adapter may invoke. Deny by default: anything
#: else is refused before a request is built. Only loadRig has been called
#: on hardware (finding 4: returns True).
ALLOWED_METHODS: frozenset[tuple[str, str]] = frozenset({
    (RIGS, "loadRig"),
})

#: Chain.ModuleType ordinals that are never written, and why (finding 1).
REFUSED_MODULE_ORDINALS: dict[int, str] = {
    20: "Neural Amp Modeler 2: writing it killed the engine on a Core at "
        "5.1.0.2a63755, reproduced twice; the API stayed down until a power "
        "cycle and no read-back catches it (finding 1)",
    254: "C-Verb 2: the write sticks but the unit publishes no object for "
         "it, so nothing downstream can address a parameter on it "
         "(finding 1)",
}

#: What the adapter can say about the unit it was measured against.
EVIDENCE: dict[str, Any] = {
    "model": "HeadRush Core",
    "firmware": "5.1.0.2a63755",
    "measured_on": "2026-09-15",
    "unverified_models": ("Prime", "Flex Prime"),
    "source": "docs/HEADRUSH-HARDWARE-FINDINGS.md",
}

SLOT_MODE = {0: SceneSlotState.NO_CHANGE, 1: SceneSlotState.ON,
             2: SceneSlotState.OFF}
MODE_VALUE = {v: k for k, v in SLOT_MODE.items()}
SCENE_MODE_NEW = 2          # ModeNew{n} == 2 means the switch is in scene mode


class NotSupported(RuntimeError):
    """A contract method with no HeadRush meaning. Stated, not faked."""


@dataclass(frozen=True)
class DerivedDisplay:
    """A display value the DEVICE NEVER SAID, and where the maths came from.

    `get_param_wire` returns a bare float because the unit sent that float.
    This does not, on purpose. The number here is computed from a curve read
    out of the vendor's editor, so handing back a bare float would let it be
    logged, planned against or shown exactly as if the unit had reported it,
    and `devices/headrush/tapers.py` exists to stop precisely that:

        "nothing downstream can present a converted number as something the
         unit said."

    `api_readable` is False for every instance. It is a field rather than a
    constant so a caller filtering on it does not have to know that.

    NOT a NamedTuple, which was the first shape here and was wrong: a tuple
    unpacks, so `value, *_ = got` and `got[0]` would hand back exactly the
    bare float this type exists to withhold. Indexing and unpacking are not
    available on a dataclass, so the invariant is enforced rather than
    described.
    """
    value: float
    text: str
    curve: str
    provenance: str
    #: Not settable. `init=False` is the point: a caller cannot construct
    #: one that claims the unit said this, and `replace()` cannot flip it.
    api_readable: bool = field(init=False, default=False)


class MethodRefused(PermissionError):
    """An object-method outside the allowlist. Raised before transport."""


class HeadrushAdapter:
    CAPABILITIES = Capabilities(
        read_path=ReadPath.DEVICE,
        observes_foreign_writes=True,
        reads_slot_names=True,
        reads_slot_state=False,
        verifies_writes=True,
        has_scenes=True,
        stores_presets=False,
        topology=Topology.SELECTED,
        has_modifiers=False,
        installs_files=False,
        can_rename=False,
        composable_scene_slots=True,
        plays_captures=False,   # #162: the Core plays no captures; declared, not implied
    )

    def __init__(self, client, registry: Registry, *,
                 topologies: topo.TopologyTable | None = None,
                 tapers: TaperTable | None = None,
                 settle_s: float = 0.5,
                 sleep: Callable[[float], None] = time.sleep):
        self.client = client
        self.registry = registry
        self._topologies = topologies or topo.load()
        #: How long a write is left before it is read back. Finding 1: ordinal
        #: 4 reads back correctly at t+0.04 s and is gone by t+0.39 s, so an
        #: immediate re-read would report success for a write the unit has
        #: already discarded. Injectable so the simulator tests do not wait.
        self.settle_s = settle_s
        self._sleep = sleep
        #: #130's curve table, or None. OPT-IN, and the default is None so
        #: that an adapter built the way every existing caller builds one
        #: refuses display conversion exactly as before. The registry's own
        #: refusal is untouched either way: it describes the API, which still
        #: publishes an opaque id and no formula, and tapers.py says in as
        #: many words that the refusal "is still correct about the API and is
        #: left alone". What this adds is a caller that HAS the other kind of
        #: knowledge and has to name where it came from.
        if tapers is not None and not isinstance(tapers, TaperTable):
            raise TypeError(
                f"tapers= takes a devices.headrush.tapers.TaperTable, not "
                f"{type(tapers).__name__}. An object that merely answers to "
                f"to_display and to_wire would be converting with a curve "
                f"nobody can name the provenance of, which is the one thing "
                f"DerivedDisplay exists to make impossible.")
        self.tapers = tapers
        self._by_ordinal = {b.module_ordinal: b
                            for b in registry.selectable_blocks()}
        self.undecoded: set[str] = set()

    # --- identity ---------------------------------------------------------

    def capabilities(self) -> Capabilities:
        return self.CAPABILITIES

    # #162: the no-support path, one line, never an error page.
    def capture_capabilities(self):
        from fm9.captures import NoCaptures
        return NoCaptures().capture_capabilities()

    def list_captures(self) -> list:
        return []

    def install_capture(self, record, raw: bytes, slot: int):
        from fm9.captures import NoCaptures
        return NoCaptures().install_capture(record, raw, slot)

    def remove_capture(self, slot: int):
        from fm9.captures import NoCaptures
        return NoCaptures().remove_capture(slot)

    def evidence(self) -> dict:
        return dict(EVIDENCE)

    def firmware_label(self) -> str:
        """The unit's own version string (/Evil/Gui AppVersion)."""
        got = self.client.get_property(GUI, "AppVersion")
        return str(got or "")

    def close(self) -> None:
        pass

    # --- the one write path -----------------------------------------------

    def _write_verified(self, path: str, name: str, value: Any) -> dict:
        """Write one property and read it back after the settle. The ONLY way
        this adapter writes a property, so read-back cannot be skipped by
        accident. Returns {ok, wanted, read} and never claims success on a
        mismatch."""
        self.client.set_property(path, name, value)
        self._sleep(self.settle_s)
        read = self.client.get_property(path, name)
        ok = read == value
        return {"ok": ok, "path": path, "name": name, "wanted": value,
                "read": read,
                "detail": (f"{path} {name} = {value!r}, read back {read!r}"
                           if ok else
                           f"{path} {name}: wrote {value!r}, unit reads "
                           f"{read!r} after {self.settle_s:g} s")}

    # --- allowlisted methods ----------------------------------------------

    def call_method(self, path: str, method: str,
                    arguments: list | None = None) -> Any:
        if (path, method) not in ALLOWED_METHODS:
            raise MethodRefused(
                f"{method} on {path} is not on this adapter's allowlist; "
                f"only {sorted(ALLOWED_METHODS)} may be invoked, and every "
                "other method is unreachable by construction")
        return self.client.call_method(path, method, arguments)

    # --- DeviceAdapter: state -----------------------------------------------

    def status_dump(self) -> dict:
        chain = self.client.get_properties(CHAIN) or {}
        rig = self.client.get_properties(RIG) or {}
        slots = {n: int(chain.get(f"ModuleType{n}", 0) or 0)
                 for n in range(1, SLOTS + 1)}
        return {"rig": rig.get("PresetName", ""),
                "routing": int(chain.get("Routing", 0) or 0),
                "slots": slots}

    def current_preset(self) -> tuple:
        """(None, rig name). Rigs are named, not numbered; the first element
        is None rather than an invented ordinal."""
        name = self.client.get_property(RIG, "PresetName")
        return (None, str(name or ""))

    def rig_id(self, preset: Any) -> tuple[str, str]:
        """(rig id, rig name) for a name or an id. /Evil/API/Rigs publishes
        AllRigNames and AllRigIds as parallel lists (measured, PR #134), so
        a name resolves without a second round trip. Unknown: LookupError."""
        rigs = self.client.get_properties(RIGS) or {}
        names = list(rigs.get("AllRigNames") or rigs.get("RigNames") or [])
        ids = list(rigs.get("AllRigIds") or rigs.get("RigIds") or [])
        key = str(preset)
        if key in ids:
            return key, names[ids.index(key)] if ids.index(key) < len(names) else key
        if key in names and names.index(key) < len(ids):
            return ids[names.index(key)], key
        raise LookupError(f"no rig named or identified {key!r} in the unit's library")

    def select_preset(self, preset: Any) -> Any:
        """Load a rig, by name or id, through the one allowlisted method.

        loadRig takes the rig ID: the name gets a 504 and loads nothing
        (#135, found by the #126 hardware pass). The read-back waits the
        settle first, because loadRig returns before the engine swaps."""
        rig_id, name = self.rig_id(preset)
        result = self.call_method(RIGS, "loadRig", [rig_id, ""])
        self._sleep(self.settle_s)
        loaded = self.client.get_property(RIG, "PresetName")
        return {"ok": loaded == name, "returned": result, "loaded": loaded,
                "rig_id": rig_id}

    def slot_name(self, preset: Any) -> Any:
        """Rig names list without loading (RigNames on /Evil/API/Rigs);
        `preset` indexes that list."""
        names = self.client.get_property(RIGS, "RigNames") or []
        try:
            return names[int(preset)]
        except (IndexError, TypeError, ValueError):
            return None

    def is_slot_empty(self, preset: Any) -> Any:
        return self.slot_name(preset) is None

    def scan_slots(self, start: int = 0, end: int = 511) -> Any:
        names = self.client.get_property(RIGS, "RigNames") or []
        return [(i, n) for i, n in enumerate(names) if start <= i <= end]

    def store_preset(self, slot: Any) -> Any:
        raise NotSupported("storing a rig has not been measured on this "
                           "device; nothing here saves anything")

    # --- DeviceAdapter: scenes ----------------------------------------------

    def scene_name(self, scene: Any = None) -> Any:
        n = int(scene) if scene is not None else self._current_scene()
        if n is None:
            return None
        return self.client.get_property(FOOTSWITCH, f"FootSwitchText{n}")

    def _current_scene(self) -> int | None:
        last = self.client.get_property(FOOTSWITCH, "LastScene")
        try:
            last = int(last)
        except (TypeError, ValueError):
            return None
        return last + 1 if last >= 0 else None      # LastScene is zero based

    def set_scene(self, scene_1based: int) -> dict:
        """Engage scene n: SceneActive{n} = True, which only applies when the
        switch is in scene mode (ModeNew{n} == 2, finding 4). Read back
        through LastScene, the one zero-based index on the surface."""
        n = int(scene_1based)
        if not 1 <= n <= SCENES:
            raise ValueError(f"scene {n} is out of range 1..{SCENES}")
        mode = self.client.get_property(FOOTSWITCH, f"ModeNew{n}")
        if mode != SCENE_MODE_NEW:
            return {"ok": False,
                    "detail": f"switch {n} is not in scene mode (ModeNew{n} "
                              f"= {mode!r}); SceneActive would be ignored"}
        # Through the one write path like every other property (review
        # round 1), so the flag IS read back and reported. Success is judged
        # on the effect the write is for: LastScene moving to n - 1 is what
        # finding 4 measured as "the scene engaged". The #126 pass (PR #134)
        # then measured SceneActive as a LATCH: engaging a second scene
        # leaves the second's flag True and clears the first's. So
        # `written` is honest evidence too, and is reported; LastScene stays
        # the success signal because it is the one that names the scene.
        w = self._write_verified(FOOTSWITCH, f"SceneActive{n}", True)
        last = self.client.get_property(FOOTSWITCH, "LastScene")
        engaged = last == n - 1
        if not w["ok"]:
            self.undecoded.add(
                f"SceneActive{n} did not read back True after the settle; on the "
                "measured Core it latches (PR #134), so this is worth a look")
        return {"ok": engaged, "written": w["ok"], "engaged": engaged,
                "detail": (f"scene {n} engaged (LastScene {last}); {w['detail']}"
                           if engaged else
                           f"scene {n} not engaged: LastScene reads {last!r} "
                           f"(wanted {n - 1}); {w['detail']}")}

    # --- SceneSlots -----------------------------------------------------------

    def scene_slots(self, scene_1based: int) -> dict:
        n = int(scene_1based)
        props = self.client.get_properties(FOOTSWITCH) or {}
        out: dict[str, SceneSlotState] = {}
        for m in range(1, SLOTS + 1):
            effect = props.get(f"Scene{n}_{m}_Effect") or ""
            if not effect:
                continue
            state = SLOT_MODE.get(int(props.get(f"Scene{n}_{m}_Mode", 0) or 0),
                                  SceneSlotState.NO_CHANGE)
            out[effect] = state
        return out

    def set_scene_slot(self, scene_1based: int, slot_name: str,
                       state: SceneSlotState) -> dict:
        n = int(scene_1based)
        state = SceneSlotState(state)
        props = self.client.get_properties(FOOTSWITCH) or {}
        target = free = None
        for m in range(1, SLOTS + 1):
            effect = props.get(f"Scene{n}_{m}_Effect") or ""
            if effect == slot_name:
                target = m
                break
            if not effect and free is None:
                free = m
        target = target if target is not None else free
        if target is None:
            return {"ok": False, "detail": f"scene {n} has no free slot for "
                                           f"{slot_name!r}"}
        a = self._write_verified(FOOTSWITCH, f"Scene{n}_{target}_Effect", slot_name)
        b = self._write_verified(FOOTSWITCH, f"Scene{n}_{target}_Mode",
                                 MODE_VALUE[state])
        return {"ok": a["ok"] and b["ok"], "detail": f"{a['detail']}; {b['detail']}"}

    # --- DeviceAdapter: blocks and parameters ---------------------------------

    def _block_for_slot(self, slot: int):
        ordinal = int(self.client.get_property(CHAIN, f"ModuleType{slot}") or 0)
        block = self._by_ordinal.get(ordinal)
        if block is None:
            raise UnknownBlock(f"slot {slot} holds ordinal {ordinal}, which "
                               "names no addressable object")
        return block

    def set_bypass(self, effect_id: int, bypassed: bool) -> dict:
        """`effect_id` is the slot number; the block's own `On` is the
        switch (finding 4 compared every scene slot against it)."""
        block = self._block_for_slot(int(effect_id))
        return self._write_verified(block.path, "On", not bool(bypassed))

    def set_channel(self, effect_id: int, channel_0based: int) -> Any:
        raise NotSupported("a HeadRush block has no channels")

    def set_param_display(self, spec: Any, display_value: float) -> Any:
        """Write a value as the unit SHOWS it, when a curve table was given.

        Without one this refuses, unchanged: the device publishes an opaque
        taper id and no formula (finding 3). With one, the display value is
        converted and then written through the same verified path as every
        other write, so the read-back still compares WIRE values. Converting
        the units neither strengthens nor weakens that check.

        WHICH MEANS `ok` IS AS WRONG HERE AS IT IS EVERYWHERE ELSE, and this
        is the first API that invites a caller to write in display units, so
        it has to say so. `ok` is exact equality against the read-back. The
        unit does not store what you send: it converts to display, snaps the
        DISPLAY value to the published grid and converts back, so on a
        quantized parameter a write the device honoured reports False. On
        `Amp.TremSpeed` every wire value tested does. That is #167, it lives
        in `_write_verified`, and until it is fixed `ok=False` from this
        method does NOT mean the write failed. `display_wanted` is what was
        asked for; read the parameter back with `get_param_display` to see
        what the unit actually holds.
        """
        if self.tapers is None:
            raise NotMeasured(
                f"{spec.block}.{spec.name}: the display value cannot be "
                f"turned into the 0..1 wire value; the curve is taper_id="
                f"{spec.taper_id!r} and the device does not say what that "
                "denotes. Write the wire value with set_param_wire instead, "
                "or build the adapter with tapers=devices.headrush.tapers"
                ".load() and accept that the curve comes from the vendor's "
                "editor rather than from the unit.")
        wire = self._converted(spec, "to_wire", float(display_value))
        out = self.set_param_wire(spec, wire)
        out["display_wanted"] = float(display_value)
        out["curve"] = self.tapers.name(spec.taper_id)
        out["provenance"] = self.tapers.provenance
        return out

    @staticmethod
    def _must_be_continuous(spec: Any) -> tuple[float, float]:
        """The published range, or the reason there is nothing to convert.

        BOTH GUARDS ARE HERE RATHER THAN ONLY IN A TEST. No parameter this
        firmware publishes carries both `options` and a display range, so the
        selector case cannot arise today - which is exactly why it is refused
        here, in code: a schema that grew one would otherwise have its ORDINAL
        run through a 0..1 taper, silently.

        There IS also a test, and it checks the other thing. This method
        refuses a selector whatever the schema says;
        `test_a_selector_is_not_dragged_onto_the_continuous_path` asserts the
        registry-wide property across every parameter, so if a future firmware
        publishes a selector WITH a display range the suite says so by name
        instead of the fact quietly ceasing to be true. Neither substitutes
        for the other: the guard is the behaviour, the test is the warning.

        Checked BEFORE the device is touched, so a spec that was never
        convertible does not cost a round trip to find out.
        """
        if getattr(spec, "options", None) is not None:
            raise NotMeasured(
                f"{spec.block}.{spec.name} is a selector with "
                f"{len(spec.options)} positions, not a continuous parameter. "
                f"Its wire value is a position, not a point on a curve; use "
                f"set_param_ordinal and Parameter.option to name one.")
        lo, hi = spec.display_minimum, spec.display_maximum
        if lo is None or hi is None:
            raise NotMeasured(
                f"{spec.block}.{spec.name}: the device publishes no display "
                f"range, so there is nothing to convert between.")
        return lo, hi

    def _converted(self, spec: Any, direction: str, value: float) -> float:
        """One conversion, with the parameter's published range and curve.

        `UnknownTaper` and `NotConvertible` are deliberately NOT caught. An id
        this table has never seen means the firmware is publishing a curve the
        table was not built against, and a value with no finite image means
        the curve genuinely has none there. Turning either into a plausible
        number is the failure this whole module exists to avoid.
        """
        lo, hi = self._must_be_continuous(spec)
        return getattr(self.tapers, direction)(
            value, minimum=lo, maximum=hi, algo=spec.taper_id)

    def set_param_wire(self, spec: Any, normalised: float) -> dict:
        lo, hi = spec.wire_range or (None, None)
        if lo is None or not lo <= float(normalised) <= hi:
            raise ValueError(f"{spec.block}.{spec.name} takes {lo}..{hi} on "
                             f"the wire, not {normalised!r}")
        block = self.registry.block(spec.block)
        return self._write_verified(block.path, spec.name, float(normalised))

    def set_param_ordinal(self, spec: Any, ordinal: int) -> dict:
        block = self.registry.block(spec.block)
        return self._write_verified(block.path, spec.name, int(ordinal))

    def get_param_display(self, spec: Any) -> Any:
        """The number the unit would SHOW, when a curve table was given.

        Returns a `DerivedDisplay`, never a bare float, because the unit did
        not say this number. `get_param_wire` returns a float precisely
        because the unit did say that one, and the asymmetry is the point.
        """
        if self.tapers is None:
            raise NotMeasured(
                f"{spec.block}.{spec.name}: the wire value cannot be shown "
                "as a display value; see set_param_display. get_param_wire "
                "reads it.")
        self._must_be_continuous(spec)
        wire = self.get_param_wire(spec)
        if wire is None:
            raise NotMeasured(
                f"{spec.block}.{spec.name}: the unit returned no wire value, "
                f"so there is nothing to convert.")
        value = self._converted(spec, "to_display", float(wire))
        return DerivedDisplay(
            value=value,
            text=self._formatted(spec, value),
            curve=self.tapers.name(spec.taper_id),
            provenance=self.tapers.provenance)

    @staticmethod
    def _formatted(spec: Any, value: float) -> str:
        """The unit's own `format` applied, or a plain number when it
        publishes none. The format string is the device's; the VALUE is not,
        which is why this never travels without the rest of DerivedDisplay."""
        fmt = spec.display_format
        if not fmt:
            return f"{value:g}{(' ' + spec.unit) if spec.unit else ''}"
        try:
            return fmt % value
        except (TypeError, ValueError):
            return f"{value:g}{(' ' + spec.unit) if spec.unit else ''}"

    def get_param_wire(self, spec: Any) -> Any:
        block = self.registry.block(spec.block)
        return self.client.get_property(block.path, spec.name)

    def bulk_read(self, effect_id: int, timeout: float = 1.5) -> Any:
        block = self._block_for_slot(int(effect_id))
        return self.client.get_properties(block.path)

    def set_params_batch(self, items: Any) -> list:
        return [self.set_param_ordinal(spec, v) if isinstance(v, int)
                else self.set_param_wire(spec, v) for spec, v in items]

    # --- ChainEditing (slot is the opaque position, #123) -----------------------

    def place_block(self, position: Any, effect_id: int) -> dict:
        """Put module ordinal `effect_id` in slot `position` (1..14), or 0 to
        empty it. Refuses the measured-dangerous ordinals before transport;
        verifies by delayed read-back AND by the module's object answering,
        because finding 1 showed each check catches a different failure."""
        slot = int(position)
        ordinal = int(effect_id)
        if not 1 <= slot <= SLOTS:
            raise ValueError(f"slot {slot} is out of range 1..{SLOTS}")
        if ordinal in REFUSED_MODULE_ORDINALS:
            raise PermissionError(
                f"refusing to write ModuleType {ordinal} "
                f"({REFUSED_MODULE_ORDINALS[ordinal]})")
        res = self._write_verified(CHAIN, f"ModuleType{slot}", ordinal)
        if not res["ok"]:
            return {"ok": False, "slot": slot, "ordinal": ordinal,
                    "detail": "not placed: " + res["detail"]}
        if ordinal == 0:
            return {"ok": True, "slot": slot, "ordinal": 0,
                    "detail": f"slot {slot} emptied"}
        block = self._by_ordinal.get(ordinal)
        path = block.path if block else None
        present = False
        if path:
            try:
                present = isinstance(self.client.get_properties(path), dict)
            except Exception:      # noqa: BLE001  a 404 is the finding itself
                present = False
        if not present:
            return {"ok": False, "slot": slot, "ordinal": ordinal,
                    "detail": f"slot {slot} reads {ordinal} but the unit "
                              f"publishes no object for it"
                              + (f" ({path} does not answer)" if path else "")}
        return {"ok": True, "slot": slot, "ordinal": ordinal,
                "detail": f"{block.name} in slot {slot}, read back and its "
                          f"object answers"}

    def reorder_block(self, moving_eid: int, ref_eid: int) -> dict:
        """Swap two slots' contents. Positions are slots, so a reorder is
        two verified ModuleType writes."""
        a, b = int(moving_eid), int(ref_eid)
        ta = int(self.client.get_property(CHAIN, f"ModuleType{a}") or 0)
        tb = int(self.client.get_property(CHAIN, f"ModuleType{b}") or 0)
        r1 = self._write_verified(CHAIN, f"ModuleType{a}", tb)
        r2 = self._write_verified(CHAIN, f"ModuleType{b}", ta)
        return {"ok": r1["ok"] and r2["ok"],
                "detail": f"{r1['detail']}; {r2['detail']}"}

    # --- TopologySelection -------------------------------------------------------

    def topologies(self) -> list:
        """The ten routing NAMES, in the unit's index order. Names only: the
        device never publishes their shapes (#109)."""
        return list(self._topologies.names())

    def current_topology(self) -> int:
        return int(self.client.get_property(CHAIN, "Routing") or 0)

    def select_topology(self, index: int) -> dict:
        self._topologies.get(int(index))     # UnknownTopology if not one
        return self._write_verified(CHAIN, "Routing", int(index))
