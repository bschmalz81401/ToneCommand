"""The HeadRush signal-path templates, as a model rather than as ten strings.

#33 phase 3. Kept in its own module because it is the one part of this device
that has no FM9 analogue at all, and because its provenance differs from
everything else here: the schema is the device describing itself, this is not.

WHAT A HEADRUSH TOPOLOGY IS

Fourteen ordered slots and one integer. The integer picks a whole prebuilt
routing out of an enumerated list of ten; there is no message that connects
slot 3 to slot 7, because connection is not addressable on this device. That is
why the shared contract gained a ranked `Topology` (fm9/adapter.py) instead of
an `edits_chain` boolean: `CONSTRUCTED` is a device where a caller draws the
connections, `SELECTED` is this one.

Flattening that into an FM9 grid would be the specific mistake this module
exists to prevent. A rig here is one of three genuinely different shapes:

    straight        fourteen slots in a line
    split/rejoin    common slots, two parallel branches, a MIX, common slots
    dual            TWO independent paths, own input and own output, never meet

The third is the one a grid cannot express. `Dual Path 4-10` is not a rig with
a branch; it is a four-slot vocal chain beside a ten-slot guitar chain, and
slot 3 on one has no ordering relationship to slot 3 on the other.

WHAT IS KNOWN, AND HOW IT IS KNOWN

The device publishes the ten NAMES on `Chain.Routing` and nothing about their
shapes. Writing each of the ten in turn and reading the chain object back
leaves every per-slot property byte-identical (#109). The shapes come from the
vendor's own editor bundle and are carried in
`config/headrush_topologies.json`, which marks itself `api_readable: false`.

So `Topology.role()` and `Topology.paths` are DECLARED KNOWLEDGE. Anything
surfacing them to a user or a planner has to say so rather than implying the
unit was asked, which is what `provenance` is for and why it is not optional.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import IntEnum
from functools import lru_cache
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent.parent / "config"
TOPOLOGIES = CONFIG / "headrush_topologies.json"

SLOTS = 14


class SlotRole(IntEnum):
    """Where a slot sits in its routing. Measured; see the module docstring.

    Named rather than left as the bare integers the bundle uses, because
    `role == 1` at a call site says nothing and `role is SlotRole.BRANCH_A`
    says the thing the reader needs.
    """

    COMMON = 0      # on the single path, or before/after a split
    BRANCH_A = 1    # the first parallel branch
    BRANCH_B = 2    # the second parallel branch


class TopologyKind(IntEnum):
    """The three shapes a rig can actually take.

    Derived from the measured fields rather than stored, so a future routing
    cannot be mislabelled by someone forgetting to set a flag.
    """

    STRAIGHT = 0    # one path, no branches
    SPLIT = 1       # one path that divides and rejoins
    DUAL = 2        # two independent paths that never meet


class UnknownTopology(LookupError):
    """Asked for a routing the device does not offer."""


@dataclass(frozen=True)
class Topology:
    """One of the ten, with its slot layout.

    Frozen because it is a description of the device and not a thing a caller
    edits: on a SELECTED device you choose a topology, you do not build one.
    """

    index: int
    name: str                       # the editor's name, e.g. "Middle Split 3-4-3"
    schema_name: str                # the device's own, e.g. "SPS-1"
    display_order: int
    requires_vocals: bool
    roles: tuple[SlotRole, ...]     # one per slot, slot N at roles[N - 1]
    paths: tuple[tuple[int, ...], ...]

    @property
    def kind(self) -> TopologyKind:
        if len(self.paths) > 1:
            return TopologyKind.DUAL
        if SlotRole.BRANCH_A in self.roles:
            return TopologyKind.SPLIT
        return TopologyKind.STRAIGHT

    def role(self, slot: int) -> SlotRole:
        """Declared, not read from the device. See the module docstring."""
        self._check(slot)
        return self.roles[slot - 1]

    def path_of(self, slot: int) -> int:
        """Which INDEPENDENT path a slot belongs to, zero based.

        Distinct from `role`, and the distinction is the one most likely to be
        got wrong: a dual rig reports COMMON for all fourteen slots, because
        role describes branches WITHIN a path and duals have none. Ask this
        when the question is "do these two slots feed each other at all".
        """
        self._check(slot)
        for i, path in enumerate(self.paths):
            if slot in path:
                return i
        raise UnknownTopology(
            f"slot {slot} belongs to no path in {self.name!r}; the topology "
            f"table is inconsistent")

    def slots_on_path(self, path: int = 0) -> tuple[int, ...]:
        if not 0 <= path < len(self.paths):
            raise UnknownTopology(
                f"{self.name!r} has {len(self.paths)} path(s); no path {path}")
        return self.paths[path]

    def branch_slots(self, branch: SlotRole) -> tuple[int, ...]:
        if branch is SlotRole.COMMON:
            raise ValueError("COMMON is not a branch; use slots_on_path()")
        return tuple(n for n, r in enumerate(self.roles, start=1) if r is branch)

    def feeds(self, upstream: int, downstream: int) -> bool | None:
        """Whether signal can reach `downstream` from `upstream`.

        Returns None rather than a guess where the honest answer is unknown.
        Two slots in the SAME parallel branch, or both common, are ordered by
        slot number and the answer is known. Across the two branches of a split
        there is no path at all, which is a known False. But the device never
        published where the split and the rejoin sit relative to a given common
        slot, so common-to-branch and branch-to-common are NOT derivable from
        this table, and saying so is the point.
        """
        self._check(upstream)
        self._check(downstream)
        if self.path_of(upstream) != self.path_of(downstream):
            return False                      # separate paths never meet
        up, down = self.role(upstream), self.role(downstream)
        if up is down:
            return upstream < downstream      # same lane, ordered by slot
        if SlotRole.COMMON not in (up, down):
            return False                      # A and B are parallel, not serial
        return None                           # crossing the split: not published

    def _check(self, slot: int) -> None:
        if not 1 <= slot <= len(self.roles):
            raise UnknownTopology(
                f"slot {slot} is out of range; this device has "
                f"{len(self.roles)} slots")


@dataclass(frozen=True)
class TopologyTable:
    """The ten, plus where they came from. Provenance is not optional."""

    provenance: str
    api_readable: bool
    warning: str
    source: str
    by_index: dict[int, Topology]

    def __len__(self) -> int:
        return len(self.by_index)

    def __iter__(self):
        return iter(self.by_index[i] for i in sorted(self.by_index))

    def get(self, index: int) -> Topology:
        try:
            return self.by_index[index]
        except KeyError:
            raise UnknownTopology(
                f"routing {index} is not one of the "
                f"{len(self.by_index)} this device offers") from None

    def names(self) -> list[str]:
        """Schema names in INDEX order, which is what Chain.Routing takes.

        Not display order. The picker draws index 5 and index 9 sixth and
        fifth, so a caller that built a menu and wrote back its position would
        select the wrong topology.
        """
        return [self.by_index[i].schema_name for i in sorted(self.by_index)]

    def in_display_order(self) -> list[Topology]:
        return sorted(self, key=lambda t: t.display_order)


@lru_cache(maxsize=1)
def load(path: Path | None = None) -> TopologyTable:
    """The committed table. Cached: it is a constant of the firmware."""
    src = path or TOPOLOGIES
    blob = json.loads(src.read_text())
    table = {}
    for r in blob["routings"]:
        roles = tuple(SlotRole(s["role"]) for s in r["slots"])
        table[r["index"]] = Topology(
            index=r["index"],
            name=r["name"],
            schema_name=r["schema_name"],
            display_order=r["display_order"],
            requires_vocals=r["requires_vocals"],
            roles=roles,
            paths=tuple(tuple(p) for p in r["paths"]),
        )
    return TopologyTable(
        provenance=blob["provenance"],
        api_readable=blob["api_readable"],
        warning=blob["warning"],
        source=blob["source"],
        by_index=table,
    )
