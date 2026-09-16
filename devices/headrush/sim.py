"""A HeadRush that exists only in this process.

#33 phase 3. Runs with no unit and no network, so phases 4 and 5 can be
reviewed by someone who does not own the hardware. That is the whole reason it
is built before the adapter rather than after: the maintainer has no HeadRush,
and an adapter only he can read is an adapter only I can check.

IT SERVES THE REAL CLIENT, RATHER THAN STANDING BESIDE IT

`HeadrushClient` takes an injected opener (#116), so this implements that
callable and the genuine client code runs against it: the same URL building,
the same empty-body-on-PUT handling, the same error text. A simulator that
reimplemented the client would agree with itself and prove nothing.

    sim = HeadrushSim()
    client = HeadrushClient("sim.local", "127.0.0.1", opener=sim.opener)
    client.get_property("/Evil/Engine/Patch/Chain", "Routing")

IT IS BUILT FROM THE COMMITTED SCHEMA, NOT FROM A HANDWRITTEN MODEL

Every object path, property name, type and range comes from
`config/headrush_schema.json`, which is the unit's own self-description. The
alternative, typing out a device model by hand, drifts the moment firmware
moves and cannot be checked against anything.

Property VALUES are not in that file on purpose (they are the owner's rig), so
the sim starts every property at a type-appropriate default and records that
those defaults are ours rather than the device's. A test that asserted a
default here was asserting something about this file, not about a HeadRush.

WHAT IT REFUSES TO PRETEND

`fm9/sim.py` earns its keep by naming undecoded territory instead of answering
smoothly, and this does the same in the two places that matter:

  - **Topology shape is declared, not simulated from the wire.** The unit
    publishes ten names and nothing about their shapes (#109), so the sim
    serves exactly what a real unit serves: the integer. Anything asking where
    the branches are is asking `topology.py`, which says where its knowledge
    came from.
  - **Unknown paths 404 rather than inventing an object.** A path outside the
    committed schema is not a blank object, it is a question this device was
    never asked.

`undecoded` collects anything the sim was asked to do that a real unit's
behaviour is not established for, so a caller can report it rather than
discovering it on hardware.
"""
from __future__ import annotations

import json
import urllib.error
from email.message import Message as HTTPMessage
from io import BytesIO
from pathlib import Path
from typing import Any

from devices.headrush import topology as topo

CONFIG = Path(__file__).resolve().parent.parent.parent / "config"
SCHEMA = CONFIG / "headrush_schema.json"

CHAIN = "/Evil/Engine/Patch/Chain"
RIG = "/Evil/Engine/Patch/Rig"
FOOTSWITCH = "/Evil/Engine/FootSwitch"
SLOTS = topo.SLOTS
SCENES = 10

#: `Scene{n}_{m}_Mode` is an integer the device publishes as 0..2 with no names
#: of its own. Which integer is which was MEASURED on a Core at fw
#: 5.1.0.2a63755 and cross-read against the unit's own bundle, recorded in
#: HeadrushRigBuilder's `SLOT_MODES`. Cited rather than inferred: the schema
#: alone would only justify "three states", and guessing the order would put
#: a scene's blocks in exactly the wrong places.
SLOT_MODE = {0: "no_change", 1: "on", 2: "off"}
MODE_VALUE = {v: k for k, v in SLOT_MODE.items()}


class SimError(Exception):
    """Raised for a request a real unit would refuse, with the HTTP status."""

    def __init__(self, status: int, message: str):
        super().__init__(f"{status} {message}")
        self.status = status
        #: The message WITHOUT the status prefix, for the wire boundary. The
        #: opener puts this in HTTPError's reason slot: urllib renders that as
        #: "HTTP Error 404: <reason>" and describe_unreachable as "The unit
        #: answered 404 <reason>", so passing str(self) produced "404 404 no
        #: object at ...". The whole point of raising urllib's type there is
        #: that callers see what production shows them.
        self.message = message


def _default_for(meta: dict) -> Any:
    """A starting value for one property, from its own published type.

    OURS, not the device's: the schema deliberately carries no values. Chosen
    to be the least surprising member of the published domain rather than
    anything claimed to be a factory setting.
    """
    kind = meta.get("type")
    if kind == "integer":
        return int(meta.get("minimum", 0))
    if kind == "number":
        return float(meta.get("minimum", 0.0))
    if kind == "boolean":
        return False
    if kind == "string":
        return ""
    return None


class HeadrushSim:
    """An in-process HeadRush, shaped by the committed schema.

    Deliberately NOT `tests/stub_device.py`. That is the contract's test
    double, a deliberately generic device used to prove the capability split is
    real. This is one specific device with fourteen slots, ten routings and a
    rig library, and #121 says to keep them apart.
    """

    def __init__(self, schema_path: Path | None = None,
                 rigs: list[str] | None = None):
        blob = json.loads((schema_path or SCHEMA).read_text())
        self.firmware: str = blob["firmware"]
        self._paths: dict[str, str] = blob["paths"]
        self._metas: dict[str, dict] = blob["metas"]
        self.topologies = topo.load()
        self.undecoded: set[str] = set()

        # values live here; the schema has none, by design
        self._values: dict[str, dict[str, Any]] = {}
        for path, meta_hash in self._paths.items():
            props = self._properties(meta_hash)
            self._values[path] = {name: _default_for(m)
                                  for name, m in props.items()}

        self.library: list[str] = list(rigs if rigs is not None
                                       else ["Init Rig"])
        self._loaded: str | None = self.library[0] if self.library else None
        self._values.setdefault(RIG, {})["PresetName"] = self._loaded or ""
        # a rig the owner has not saved reads as an empty name on a real unit

    # --- schema -----------------------------------------------------------

    def _properties(self, meta_hash: str) -> dict[str, dict]:
        """One object's property metas, which may legitimately be none.

        `properties` is present and NULL for an object that carries only
        methods; the tree has one such object on this firmware. That is an
        object with nothing to read, not a malformed meta, so it gets an empty
        mapping rather than an exception.
        """
        meta = self._metas[meta_hash]
        props = meta.get("properties")
        if props is None:
            return {}
        return {k: v for k, v in props.items() if isinstance(v, dict)}

    def meta_for(self, path: str) -> dict[str, dict]:
        if path not in self._paths:
            raise SimError(404, f"no object at {path}")
        return self._properties(self._paths[path])

    # --- the device's own surface ----------------------------------------

    def get_properties(self, path: str) -> dict[str, Any]:
        if path not in self._values:
            raise SimError(404, f"no object at {path}")
        return dict(self._values[path])

    def set_properties(self, path: str, values: dict[str, Any]) -> None:
        if path not in self._values:
            raise SimError(404, f"no object at {path}")
        meta = self.meta_for(path)
        for name, value in values.items():
            if name not in meta:
                raise SimError(400, f"{path} has no property {name!r}")
            self._check(path, name, meta[name], value)
            self._values[path][name] = value

    def _check(self, path: str, name: str, meta: dict, value: Any) -> None:
        kind = meta.get("type")
        if kind == "integer" and not isinstance(value, int):
            raise SimError(400, f"{name} takes an integer")
        if kind == "boolean" and not isinstance(value, bool):
            raise SimError(400, f"{name} takes a boolean")
        if kind in ("integer", "number"):
            low, high = meta.get("minimum"), meta.get("maximum")
            if low is not None and value < low:
                raise SimError(400, f"{name} is below its minimum {low}")
            if high is not None and value > high:
                raise SimError(400, f"{name} is above its maximum {high}")

    # --- rig library ------------------------------------------------------

    @property
    def loaded_rig(self) -> str | None:
        return self._loaded

    def load_rig(self, name: str) -> None:
        """Select a rig by name, as a real unit does by its library.

        The chain is reset because on hardware loading a rig replaces it. The
        SLOT CONTENTS of a stored rig are not modelled: the schema carries no
        values, so this sim has no library of real rigs to restore, and
        pretending otherwise would invent preset data.
        """
        if name not in self.library:
            raise SimError(404, f"no rig named {name!r} in the library")
        self._loaded = name
        self._values[RIG]["PresetName"] = name
        for n in range(1, SLOTS + 1):
            self._values[CHAIN][f"ModuleType{n}"] = 0     # Empty Slot
        self.undecoded.add(
            "load_rig restores an empty chain: stored rig CONTENTS are not "
            "modelled, because the committed schema carries no values")

    # --- topology ---------------------------------------------------------

    @property
    def routing(self) -> int:
        return int(self._values[CHAIN]["Routing"])

    @property
    def topology(self) -> topo.Topology:
        """The current template as a model.

        Note what this is NOT: a read of the device's shape. The unit answers
        `Routing` with an integer and says nothing else, exactly as the sim
        does. The shape comes from the topology table, which declares where it
        came from.
        """
        return self.topologies.get(self.routing)

    def select_topology(self, index: int) -> None:
        self.topologies.get(index)          # raises UnknownTopology if not one
        self.set_properties(CHAIN, {"Routing": index})

    # --- slots ------------------------------------------------------------

    def slot(self, n: int) -> int:
        if not 1 <= n <= SLOTS:
            raise SimError(400, f"slot {n} is out of range 1..{SLOTS}")
        return int(self._values[CHAIN][f"ModuleType{n}"])

    def place(self, n: int, module_type: int) -> None:
        if not 1 <= n <= SLOTS:
            raise SimError(400, f"slot {n} is out of range 1..{SLOTS}")
        self.set_properties(CHAIN, {f"ModuleType{n}": module_type})

    def occupied(self) -> dict[int, int]:
        return {n: t for n in range(1, SLOTS + 1)
                if (t := self.slot(n)) != 0}

    # --- scenes -----------------------------------------------------------

    def scene_slots(self, scene_1based: int) -> dict[str, str]:
        """Slot NAME to state, read off the device's own scene properties.

        Not a side table: `Scene{n}_{m}_Effect` and `Scene{n}_{m}_Mode` are
        real properties on /Evil/Engine/FootSwitch, 140 of each, and this
        serves those. Which is what makes the scene model schema-derived
        rather than something this file invented.

        Named, not positional, and tri-state: a slot the scene says nothing
        about is absent from the mapping, which is not the same as `off`.
        """
        self._check_scene(scene_1based)
        out: dict[str, str] = {}
        props = self._values[FOOTSWITCH]
        for m in range(1, SLOTS + 1):
            effect = props.get(f"Scene{scene_1based}_{m}_Effect") or ""
            if not effect:
                continue
            mode = int(props.get(f"Scene{scene_1based}_{m}_Mode", 0))
            state = SLOT_MODE[mode]
            if state != "no_change":
                out[effect] = state
        return out

    def set_scene_slot(self, scene_1based: int, slot_name: str,
                       state: str) -> None:
        self._check_scene(scene_1based)
        if state not in MODE_VALUE:
            raise SimError(400, f"{state!r} is not a scene slot state")
        if not slot_name:
            raise SimError(400, "a scene slot is addressed by name")
        props = self._values[FOOTSWITCH]
        target = None
        free = None
        for m in range(1, SLOTS + 1):
            effect = props.get(f"Scene{scene_1based}_{m}_Effect") or ""
            if effect == slot_name:
                target = m
                break
            if not effect and free is None:
                free = m
        if target is None:
            target = free
        if target is None:
            raise SimError(400, f"scene {scene_1based} has no free slot for "
                                f"{slot_name!r}; all {SLOTS} are claimed")
        self.set_properties(FOOTSWITCH, {
            f"Scene{scene_1based}_{target}_Effect": slot_name,
            f"Scene{scene_1based}_{target}_Mode": MODE_VALUE[state],
        })

    def _check_scene(self, scene_1based: int) -> None:
        if not 1 <= scene_1based <= SCENES:
            raise SimError(400, f"scene {scene_1based} is out of range "
                                f"1..{SCENES}")

    # --- the client's opener ---------------------------------------------

    def opener(self, url: str, method: str, body: bytes | None,
               headers: dict[str, str], timeout: float) -> bytes:
        """Serve `HeadrushClient`, so the real client code is what runs.

        Failures leave here as `urllib.error.HTTPError`, not as `SimError`.
        The Opener contract in client.py says an opener "raises urllib's own
        exceptions so that callers, and describe_unreachable, see exactly what
        production sees", and a simulator that raised its own type would let
        phase 4 be written against an exception hardware never throws. The
        in-process helpers still raise SimError, which is the right type for
        calling the sim directly.
        """
        try:
            return self._serve(url, method, body)
        except SimError as err:
            raise urllib.error.HTTPError(
                url, err.status, err.message, hdrs=HTTPMessage(),
                fp=BytesIO(str(err).encode())) from err

    def _serve(self, url: str, method: str, body: bytes | None) -> bytes:
        try:
            path = url.split("/api/v1", 1)[1]
        except IndexError:
            raise SimError(404, f"not an API url: {url}") from None

        if path.startswith("/object-properties"):
            target = path[len("/object-properties"):] or "/"
            if method == "GET":
                return json.dumps(self.get_properties(target)).encode()
            if method == "PUT":
                self.set_properties(target, json.loads(body or b"{}"))
                return b""            # a real unit answers 200 with no body
            raise SimError(405, f"{method} on object-properties")

        if path.startswith("/subtree"):
            target = path[len("/subtree"):] or "/"
            prefix = "" if target in ("", "/") else target
            out = {p: {"meta": {"properties": self._properties(h)}}
                   for p, h in self._paths.items() if p.startswith(prefix)}
            if not out:
                raise SimError(404, f"no objects under {target}")
            return json.dumps(out).encode()

        if path.startswith("/object-method"):
            self.undecoded.add(
                f"object-method invoked ({path}): no method's behaviour is "
                f"established here, and #125 gates these behind a "
                f"deny-by-default allowlist")
            raise SimError(501, "object-method is not simulated")

        raise SimError(404, f"unknown endpoint {path}")
