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
        NotMeasured: the device names the curve without describing it

That last one is the point of the module. The device publishes a range, a unit
and a 0..1 wire for every continuous parameter, so the conversion LOOKS like
arithmetic. It also publishes an opaque curve id per parameter
(`x-options.normalizeAlgo`, carried here as `taper_id`) and never says what an
id denotes. Measured on hardware at this firmware, Amp.Bass carries no id and
is linear (wire 0.75 reads 75 % of 0..100), while Amp.TremSpeed carries id 5
and is quadratic (wire 0.25 and 0.5 read 1.48 and 5.19 Hz of 0.25..20, where
linear would give 5.19 and 10.125). Two curves on one block, named but not
described. So `to_display` exists only to refuse, in the one place a caller
would otherwise write the assumption themselves.

Nor is unit a safe proxy for taper: C2_Bass_Chorus.Depth is a percentage
carrying id 6.

EVERY PUBLISHED FIELD IS CARRIED, including ones nothing here interprets, and
`Parameter.published` is the verbatim record. An earlier version kept only the
fields it had a use for, and the registry then told callers the taper was
unpublished while the schema it was generated from carried normalizeAlgo. A
test now proves the set is complete against the schema rather than trusting a
list to stay in step.

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


class RegistryCorrupt(RuntimeError):
    """The registry file is internally inconsistent.

    Distinct from SchemaDrift, which is about the registry disagreeing with a
    schema that moved under it. This one is the file disagreeing with ITSELF,
    a block naming a parameter set the file does not hold, and the two want
    different responses: drift wants a regenerate-and-read-the-diff, and this
    wants the file re-fetched or restored because it was truncated or edited.
    """


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

    @property
    def published(self) -> dict:
        """Everything the device said about this property, verbatim.

        The registry stores this and nothing that repeats it, so the accessors
        below are views rather than a second copy that could disagree. Keys
        this module does not interpret are still here, on purpose: an earlier
        version kept only the fields it had a use for and the registry then
        declared the taper unpublished while the device was publishing one.
        """
        return self.raw.get("published", {})

    @property
    def read_only(self) -> bool:
        """Whether the device refuses writes. 491 properties on this firmware
        are read only, /Evil/Gui.DeviceName among them."""
        return bool(self.raw.get("read_only"))

    # --- continuous ----------------------------------------------------
    @property
    def display_minimum(self) -> float | None:
        return self.raw.get("display_minimum")

    @property
    def display_maximum(self) -> float | None:
        return self.raw.get("display_maximum")

    @property
    def display_format(self) -> str | None:
        return self.published.get("format")

    @property
    def grid(self) -> float | None:
        """The device's own step size, carried under its own name.

        NOT called display_step, because that would be a claim. It tracks the
        format's decimal precision for 1841 of the 1881 properties that have
        both and diverges for 40 (Freq1 formats as %.0f Hz and grids at 10.0),
        so it is a real step and its exact meaning is the device's to state.
        """
        return self.published.get("grid")

    @property
    def unit(self) -> str | None:
        """As the DEVICE spells it, inconsistencies included.

        The firmware writes both 'dB' and 'db', both 'Hz' and 'hz'. Tidying
        them here would put this module's opinion where the device's own string
        belongs, and a caller comparing against a unit it read from the unit
        would then miss.
        """
        from tools.build_headrush_registry import unit_of
        return unit_of(self.display_format)

    @property
    def default_normalised(self) -> float | None:
        return self.published.get("default")

    @property
    def taper_id(self) -> int | None:
        """The device's OPAQUE curve identifier (x-options.normalizeAlgo).

        It says two parameters share a taper, or do not. It never says what
        either curve is, which is why `to_display` refuses even though this is
        published. Measured on this firmware: the three Amp knobs that read
        linear carry no id, and TremSpeed, which reads quadratic, carries 5.
        Four parameters on one block is not a decoding, and nothing here treats
        absent as meaning identity.
        """
        return self.published.get("normalizeAlgo")

    @property
    def wire_range(self) -> tuple[float, float] | None:
        """What a WRITE takes: 0..1, measured, for every continuous parameter.

        Distinct from display_minimum/display_maximum, which are what the unit
        SHOWS. Conflating the two is the specific error this split exists to
        prevent, and it is an easy one: on Amp.Bass the display range is
        0..100 and writing 75 is accepted by the device without complaint.

        Measured directly on Amp and generalised from the schema's own
        defaults, not from the four readings: see `Registry.wire_encoding`,
        which states which part is which.
        """
        return (0.0, 1.0) if self.kind == "continuous" else None

    def to_display(self, normalised: float) -> float:
        """Refuses. The device names the curve without describing it.

        Kept as a method rather than omitted so that the refusal lands where a
        caller would otherwise write `lo + x * (hi - lo)` themselves.
        """
        raise NotMeasured(
            f"{self.block}.{self.name}: the device publishes a display range "
            f"({self.display_minimum}..{self.display_maximum} "
            f"{self.unit or ''}".rstrip() + f"), takes 0..1 on the wire, and "
            f"names this parameter's curve only as taper_id={self.taper_id!r}. "
            "It never says what an id denotes, so the FORMULA is unavailable "
            "and cannot be evaluated. Assuming linear is not safe by unit "
            "either: C2_Bass_Chorus.Depth is a percentage carrying id 6. "
            "Measure it (#126).")

    # --- selector and switch -------------------------------------------
    @property
    def options(self) -> list[str] | None:
        """The device's own option names, or None where it publishes none."""
        strings = self.published.get("strings")
        return list(strings) if strings else None

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
            raise RegistryCorrupt(
                f"{path} references parameter set {raw['parameters']!r}, "
                f"which this registry does not hold. The file is internally "
                f"inconsistent, which a regenerate will not diagnose: restore "
                f"it from git, or re-run tools/build_headrush_registry.py to "
                f"replace it wholesale.") from None
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
