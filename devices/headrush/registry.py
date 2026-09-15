"""The HeadRush block and parameter registry, as a lookup rather than a blob.

#33 phase 3, #122. `config/headrush_registry.json` is generated from the
committed schema by tools/build_headrush_registry.py; this is the module a
caller uses, and the place the drift guard lives.

WHAT A CALLER GETS, AND WHAT IT DELIBERATELY CANNOT GET

    reg = load()
    reg.resolve("Amp", "Bass").unit            -> '%'
    reg.resolve("Amp", "Type").options[0]      -> '59 Deluxe Gain Mod'
    reg.module_ordinal("Amp")                  -> 1

    reg.resolve("Amp", "Bass").to_display(0.75)
        NotMeasured: the taper is not published

That last one is the point of the module. The device publishes a range and a
unit for every continuous parameter and takes 0..1 on the wire, so the
conversion LOOKS like arithmetic. It is not: Amp.TremSpeed reads 5.19 Hz at
wire 0.5 on a published 0.25..20 range, where linear would give 10.125. A
`to_display` that assumed linear would be correct on every percentage control
and wrong on every frequency, time and tempo one, so the method exists only to
refuse in the one place a caller would otherwise write the assumption itself.

THE DRIFT GUARD

The registry is derived data with a real failure mode: someone regenerates
`headrush_schema.json` from a newer firmware, the registry keeps the old
ranges, and every answer stays confidently stale. So `load()` verifies that the
committed schema still hashes to what the registry was built from and raises
`SchemaDrift` naming what moved. Nothing here can tell whether the NEW schema
is right; what it can do is refuse to serve answers derived from an old one.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent.parent / "config"
REGISTRY = CONFIG / "headrush_registry.json"
SCHEMA = CONFIG / "headrush_schema.json"


class SchemaDrift(RuntimeError):
    """The registry was built from a schema the repo no longer holds."""


class UnknownBlock(LookupError):
    """No such object, or a name that more than one object answers to."""


class UnknownParameter(LookupError):
    """The object exists and publishes no such property."""


class NotMeasured(NotImplementedError):
    """Asked for something this device has not been observed to say."""


@dataclass(frozen=True)
class Parameter:
    """One published property. Frozen: it describes the device."""

    block: str
    name: str
    kind: str
    raw: dict

    # --- continuous ----------------------------------------------------
    @property
    def display_minimum(self) -> float | None:
        return self.raw.get("display_minimum")

    @property
    def display_maximum(self) -> float | None:
        return self.raw.get("display_maximum")

    @property
    def display_format(self) -> str | None:
        return self.raw.get("display_format")

    @property
    def unit(self) -> str | None:
        """As the DEVICE spells it, inconsistencies included.

        The firmware writes both 'dB' and 'db', both 'Hz' and 'hz'. Tidying
        them here would put this module's opinion where the device's own string
        belongs, and a caller comparing against a unit it read from the unit
        would then miss.
        """
        return self.raw.get("unit")

    @property
    def default_normalised(self) -> float | None:
        return self.raw.get("default_normalised")

    @property
    def wire_range(self) -> tuple[float, float] | None:
        """What a WRITE takes: 0..1, measured, for every continuous parameter.

        Distinct from display_minimum/display_maximum, which are what the unit
        SHOWS. Conflating the two is the specific error this split exists to
        prevent, and it is an easy one: on Amp.Bass the display range is
        0..100 and writing 75 is accepted by the device without complaint.
        """
        return (0.0, 1.0) if self.kind == "continuous" else None

    def to_display(self, normalised: float) -> float:
        """Refuses. The taper is not published and is not uniformly linear.

        Kept as a method rather than omitted so that the refusal lands where a
        caller would otherwise write `lo + x * (hi - lo)` themselves.
        """
        raise NotMeasured(
            f"{self.block}.{self.name}: the device publishes a display range "
            f"({self.display_minimum}..{self.display_maximum} "
            f"{self.unit or ''}".rstrip() + ") and takes 0..1 on the wire, but "
            "not the curve between them, and it is not uniformly linear: "
            "Amp.TremSpeed reads 5.19 Hz at wire 0.5 on a 0.25..20 range "
            "where linear would be 10.125. Measure it on hardware (#126) "
            "rather than assuming a shape.")

    # --- selector and switch -------------------------------------------
    @property
    def options(self) -> list[str] | None:
        """The device's own option names, or None where it publishes none."""
        value = self.raw.get("options", self.raw.get("labels"))
        return list(value) if value else None

    def option(self, ordinal: int) -> str:
        options = self.options
        if options is None:
            raise NotMeasured(
                f"{self.block}.{self.name} publishes no names for its "
                f"positions on this firmware")
        if not 0 <= ordinal < len(options):
            raise UnknownParameter(
                f"{self.block}.{self.name} has {len(options)} positions; "
                f"no position {ordinal}")
        return options[ordinal]


@dataclass(frozen=True)
class Block:
    """One addressable object and its parameters."""

    path: str
    name: str
    module_ordinal: int | None
    category: None
    parameters: dict[str, Parameter]

    @property
    def selectable(self) -> bool:
        """Whether a chain slot can hold this. False for the patch furniture
        (Chain, Input, Output, Mix) which exists but no slot selects."""
        return self.module_ordinal is not None

    def parameter(self, name: str) -> Parameter:
        try:
            return self.parameters[name]
        except KeyError:
            raise UnknownParameter(
                f"{self.name} publishes no property {name!r}; it has "
                f"{len(self.parameters)}") from None


@dataclass(frozen=True)
class Registry:
    firmware: str
    schema_fingerprint: str
    wire_encoding: dict
    blocks: dict[str, Block]
    by_name: dict[str, tuple[str, ...]]
    roster: dict[int, str]
    unbacked: tuple[dict, ...]

    def block(self, key: str) -> Block:
        """By path, or by the leaf name when that is unambiguous.

        Leaf names are NOT unique across the tree: /Evil/Engine/Patch/Input and
        /Evil/Engine/AudioCtrl/Input both answer to 'Input'. An ambiguous name
        raises with the candidates rather than picking whichever sorted first.
        """
        if key in self.blocks:
            return self.blocks[key]
        paths = self.by_name.get(key, ())
        if not paths:
            raise UnknownBlock(f"no object named or pathed {key!r}")
        if len(paths) > 1:
            raise UnknownBlock(
                f"{key!r} names {len(paths)} objects: {', '.join(paths)}. "
                f"Ask by path.")
        return self.blocks[paths[0]]

    def resolve(self, block: str, parameter: str) -> Parameter:
        """The planner-facing lookup: a block and a knob, by the names a
        player and the device both use."""
        return self.block(block).parameter(parameter)

    def module_ordinal(self, block: str) -> int:
        """What to write into Chain.ModuleType{n} to put this in a slot."""
        found = self.block(block)
        if found.module_ordinal is None:
            raise UnknownBlock(
                f"{found.name} is not in the ModuleTypes roster, so no chain "
                f"slot selects it")
        return found.module_ordinal

    def module_name(self, ordinal: int) -> str:
        try:
            return self.roster[ordinal]
        except KeyError:
            raise UnknownBlock(
                f"ordinal {ordinal} is not one of the {len(self.roster)} the "
                f"roster publishes") from None

    def selectable_blocks(self) -> list[Block]:
        return sorted((b for b in self.blocks.values() if b.selectable),
                      key=lambda b: b.module_ordinal)


def _fingerprint(schema: dict) -> str:
    # Imported lazily so devices/ does not depend on tools/ at import time.
    from tools.build_headrush_registry import fingerprint
    return fingerprint(schema)


@lru_cache(maxsize=1)
def load(registry: Path | None = None, schema: Path | None = None,
         check_drift: bool = True) -> Registry:
    blob = json.loads((registry or REGISTRY).read_text())

    if check_drift:
        schema_path = schema or SCHEMA
        current = json.loads(schema_path.read_text())
        _guard(blob, current, schema_path)

    roster = {}
    blocks = {}
    paramsets = blob["paramsets"]
    for path, raw in blob["blocks"].items():
        try:
            published = paramsets[raw["parameters"]]
        except KeyError:
            raise SchemaDrift(
                f"{path} references parameter set {raw['parameters']!r}, "
                f"which the registry does not hold. Re-run "
                f"tools/build_headrush_registry.py.") from None
        params = {n: Parameter(block=raw["name"], name=n, kind=p["kind"], raw=p)
                  for n, p in published.items()}
        blocks[path] = Block(
            path=path,
            name=raw["name"],
            module_ordinal=raw["module_ordinal"],
            category=raw["category"],
            parameters=params,
        )
        if raw["module_ordinal"] is not None:
            roster[raw["module_ordinal"]] = raw["name"]
    for gap in blob["unbacked_modules"]:
        roster[gap["ordinal"]] = gap["name"]

    by_name: dict[str, list[str]] = {}
    for path, block in blocks.items():
        by_name.setdefault(block.name, []).append(path)

    return Registry(
        firmware=blob["firmware"],
        schema_fingerprint=blob["schema_fingerprint"],
        wire_encoding=blob["wire_encoding"],
        blocks=blocks,
        by_name={n: tuple(sorted(p)) for n, p in by_name.items()},
        roster=roster,
        unbacked=tuple(blob["unbacked_modules"]),
    )


def _guard(blob: dict, schema: dict, schema_path: Path) -> None:
    """Refuse to serve answers derived from a schema the repo no longer holds.

    The diagnostic names what moved rather than only that something did,
    because the two likely causes want opposite responses: a firmware bump
    means regenerate both files and read the diff, while an unchanged firmware
    with a changed hash means the schema was edited by hand, which #117 says
    not to do.
    """
    if schema.get("device") != "headrush":
        raise SchemaDrift(
            f"{schema_path} is for device {schema.get('device')!r}, not headrush")

    expected = blob["schema_fingerprint"]
    actual = _fingerprint(schema)
    if actual == expected:
        return

    was, now = blob["firmware"], schema.get("firmware")
    if was != now:
        raise SchemaDrift(
            f"the registry was built from firmware {was}, and {schema_path} "
            f"now holds {now}. Ranges, options and ordinals all move between "
            f"firmwares. Re-run tools/build_headrush_registry.py and read the "
            f"diff before trusting either file.")
    raise SchemaDrift(
        f"{schema_path} still says firmware {now} but no longer hashes to "
        f"what the registry was built from ({expected}, now {actual}). Same "
        f"firmware with different content means the schema was edited rather "
        f"than regenerated, which tools/build_headrush_schema.py says not to "
        f"do. Re-fetch it from a unit.")
