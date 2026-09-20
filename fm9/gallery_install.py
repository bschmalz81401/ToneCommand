"""One click installs an artist pack (issue #155, J2), relocating its cabs
when their slots are taken and repointing the preset (issue #164).

Two halves. `plan` decides where everything goes before anything is
written, from what the unit itself reports (the per-slot cab name read,
the per-slot preset name read) and the owner's two whitelists; every
refusal is decided here, in one line. `execute` then performs the plan
through the existing guarded primitives only: `install_user_cab_slot`
(name read-back), the preset install path (loaded and read back), the
Cab block repoint (parameter writes read back per channel), `store_preset`
(whitelisted). It stops at the first failure and says what landed.
"""
from __future__ import annotations

import time

from dataclasses import dataclass, field
from typing import Any, Callable

from . import protocol as p

USER_BANK = 2            # the Cab block's bank id for the player's own IRs
CAB_EFFECT_ID = 62       # Cab 1
CAB_CHANNELS = 4
BANK_PARAMS = (0, 1, 2, 3)      # CABINET_BANK1..4
TYPE_PARAMS = (4, 5, 6, 7)      # CABINET_TYPE1..4
#: The block's TYPE (FM9-Edit's Dyna-Cab / Legacy dropdown): CABINET_MODE,
#: 0 = Legacy (the IR fields above are what plays), 1 = Dyna-Cab (they are
#: ignored). Pinned 2026-09-19 on firmware 11.00 by a before/after read of
#: the whole block around one FM9-Edit save; a pre-Dyna-Cab pack preset
#: loads as Dyna-Cab, so a repointed IR is not heard until this is 0.
MODE_PARAM = 31
MODE_LEGACY, MODE_DYNACAB = 0, 1


class GalleryInstallError(ValueError):
    """One line, written for the person who clicked."""


@dataclass
class CabStep:
    file: str
    name: str
    raw: bytes
    wanted: int | None          # the bundle map's slot, or None for a bare cab
    slot: int                   # where it goes
    moved: bool = False         # slot != wanted
    kept: bool = False          # already there by name, not rewritten
    editor: str = ""

    def __post_init__(self):
        self.editor = f"U1.{self.slot + 1:04d}"


@dataclass
class Plan:
    entry_id: str
    artist: str
    kind: str
    cabs: list[CabStep]
    preset_raw: bytes | None
    preset_name: str
    store_slot: int | None
    notes: list[str] = field(default_factory=list)
    replaced: str | None = None      # the preset the store slot held, if any

    @property
    def moves(self) -> dict[int, int]:
        """old slot -> new slot for every relocated cab (the repoint map)."""
        return {c.wanted: c.slot for c in self.cabs
                if c.moved and c.wanted is not None}


def _label(slot: int) -> str:
    return f"U1.{slot + 1:04d}"


def plan(entry: dict, members: list[dict], *, cab_name: Callable[[int], str | None],
         store_name: Callable[[int], str | None], cab_whitelist: set[int],
         store_whitelist: set[int]) -> Plan:
    """Decide destinations from the unit's own reports and the whitelists.

    `members` are the catalog-listed files already parsed by the fetch path:
    {"kind": "preset"|"cab", "file", "name", "raw", "wanted": slot|None}.
    Refuses, in one line and before any write: effect blocks only, no
    preset, no free whitelisted store slot, no free whitelisted cab slot.
    """
    artist = ", ".join(entry.get("artists") or [])
    kind = str(entry.get("kind") or "")
    presets = [m for m in members if m["kind"] == "preset"]
    cabs = [m for m in members if m["kind"] == "cab"]
    if not presets and not cabs:
        blocks = [m for m in members if m["kind"] == "block"]
        if blocks:
            raise GalleryInstallError(
                f"{artist}'s pack is effect blocks only; blocks are not "
                "installable from here yet")
        raise GalleryInstallError(f"{artist}'s pack holds nothing this unit "
                                  "can take")
    if not presets:
        raise GalleryInstallError(
            f"{artist}'s pack has cabs but no FM9 preset; nothing installed")
    if len(presets) > 1:
        # a pack with several presets: the first bundle's, said so
        pass
    preset = presets[0]

    # cabs: honour the map's slot when whitelisted and free (or already
    # holding this cab by name); otherwise the lowest free whitelisted slot
    taken: set[int] = set()
    steps: list[CabStep] = []
    free_cache: dict[int, str | None] = {}

    def name_at(slot: int) -> str | None:
        if slot not in free_cache:
            free_cache[slot] = cab_name(slot)
        return free_cache[slot]

    def is_free(slot: int) -> bool:
        n = name_at(slot)
        return bool(n) and p.is_empty_slot_name(n)

    for m in cabs:
        wanted = m.get("wanted")
        if wanted is not None and wanted in cab_whitelist and wanted not in taken:
            held = name_at(wanted)
            if held and held.strip() == str(m["name"]).strip():
                steps.append(CabStep(m["file"], m["name"], m["raw"], wanted,
                                     wanted, kept=True))
                taken.add(wanted)
                continue
            if is_free(wanted):
                steps.append(CabStep(m["file"], m["name"], m["raw"], wanted, wanted))
                taken.add(wanted)
                continue
        dest = next((s for s in sorted(cab_whitelist)
                     if s not in taken and is_free(s)), None)
        if dest is None:
            raise GalleryInstallError(
                f"no free user-cab slot in TONECOMMAND_CAB_SLOTS for "
                f"{m['name']}; free one or widen the list. Nothing installed")
        steps.append(CabStep(m["file"], m["name"], m["raw"], wanted, dest,
                             moved=wanted is not None and dest != wanted))
        taken.add(dest)

    # the preset: an empty whitelisted store slot when there is one, else
    # the lowest whitelisted slot (the whitelist is, by definition, the
    # slots the owner marked safe to overwrite) and the line says what it
    # replaced
    if not store_whitelist:
        raise GalleryInstallError(
            "no store slots configured (TONECOMMAND_STORE_SLOTS); nothing "
            "installed")
    replaced = None
    store = next((s for s in sorted(store_whitelist)
                  if (lambda n: bool(n) and p.is_empty_slot_name(n))(store_name(s))),
                 None)
    if store is None:
        store = sorted(store_whitelist)[0]
        held = store_name(store)
        if not held:
            raise GalleryInstallError(
                f"store slot {p.slot_label(store)} did not answer its name; "
                "not overwriting a slot the unit will not describe. Nothing "
                "installed")
        replaced = held
    notes = []
    if len(presets) > 1:
        notes.append(f"{len(presets)} presets in the pack; installing "
                     f"{preset['name']!r} first")
    return Plan(str(entry.get("id")), artist, kind, steps, preset["raw"],
                str(preset["name"]), store, notes, replaced)


def result_line(pl: Plan, store_label: str) -> str:
    head = f"{pl.artist}'s {pl.kind or 'pack'} is on slot {store_label}"
    if pl.replaced:
        head += f" (replacing {pl.replaced!r})"
    if not pl.cabs:
        return head + "."
    parts = []
    for c in pl.cabs:
        piece = f"cab {c.name} in user cab {c.editor}"
        if c.moved and c.wanted is not None:
            piece += f" (moved from {_label(c.wanted)})"
        elif c.kept:
            piece += " (already there)"
        parts.append(piece)
    return head + "; " + "; ".join(parts) + "."


#: The unit applies a write asynchronously; a read fired inside its settle
#: window returns the pre-write value (#12). The same settle-and-retry the
#: verified setters use (device.set_param_display), because a read-back that
#: only passes when enough incidental time happens to go by is not a
#: read-back: the repoint test passed here on parse time alone and failed
#: on a faster machine every time (#169).
READ_BACK_SETTLE = 0.15
READ_BACK_TRIES = 4


def _read_back(fm9: Any, spec: Any, channel: int, want: int) -> int | None:
    got = None
    for _ in range(READ_BACK_TRIES):
        time.sleep(READ_BACK_SETTLE)
        got = fm9.get_param_wire(spec, channel=channel)
        if got == want:
            return got
    return got


def repoint(fm9: Any, moves: dict[int, int]) -> list[dict]:
    """Rewrite the loaded preset's Cab block: every CABINET_TYPEn on every
    channel whose CABINET_BANKn is USER and whose value is an old slot is set
    to the new slot, in the edit buffer, and read back. Returns what changed.
    The block's channel is put back where it was."""
    if not moves:
        return []
    reg = fm9.reg
    values = fm9.bulk_read(CAB_EFFECT_ID)
    if not values:
        raise GalleryInstallError("could not read the preset's Cab block to "
                                  "repoint it; the preset was not stored")
    stride = len(values) // CAB_CHANNELS
    changed = []
    original_channel = fm9.get_channel(CAB_EFFECT_ID)
    try:
        for ch in range(CAB_CHANNELS):
            base = ch * stride
            touched = False
            for bank_p, type_p in zip(BANK_PARAMS, TYPE_PARAMS):
                bank, slot = values[base + bank_p], values[base + type_p]
                if bank == USER_BANK and slot in moves:
                    new = moves[slot]
                    if fm9.get_channel(CAB_EFFECT_ID) != ch:
                        fm9.set_channel(CAB_EFFECT_ID, ch)
                    spec = reg.spec("CABINET", type_p, 1)
                    fm9.set_param_ordinal(spec, new)
                    got = _read_back(fm9, spec, ch, new)
                    if got != new:
                        raise GalleryInstallError(
                            f"repointing the Cab block on channel {'ABCD'[ch]} "
                            f"read back {got}, not {new}; the preset was not "
                            "stored")
                    changed.append({"channel": "ABCD"[ch], "ir_slot": type_p - 3,
                                    "from": slot, "to": new})
                    touched = True
            # a channel that now points at a user IR must be in Legacy type,
            # or the IR is never heard (a pre-Dyna-Cab pack loads as Dyna-Cab)
            if touched and len(values) > base + MODE_PARAM and \
                    values[base + MODE_PARAM] != MODE_LEGACY:
                if fm9.get_channel(CAB_EFFECT_ID) != ch:
                    fm9.set_channel(CAB_EFFECT_ID, ch)
                mode = reg.spec("CABINET", MODE_PARAM, 1)
                fm9.set_param_ordinal(mode, MODE_LEGACY)
                got = _read_back(fm9, mode, ch, MODE_LEGACY)
                if got != MODE_LEGACY:
                    raise GalleryInstallError(
                        f"setting the Cab block to Legacy type on channel "
                        f"{'ABCD'[ch]} read back {got}; the preset was not stored")
                changed.append({"channel": "ABCD"[ch], "type": "legacy",
                                "from": values[base + MODE_PARAM], "to": MODE_LEGACY})
    finally:
        if original_channel is not None and \
                fm9.get_channel(CAB_EFFECT_ID) != original_channel:
            fm9.set_channel(CAB_EFFECT_ID, original_channel)
    return changed


def execute(pl: Plan, fm9: Any) -> dict:
    """Perform the plan through the guarded primitives, in order: cabs (each
    read back by name), the preset into the EDIT BUFFER, the Cab block
    repointed and read back there, then ONE store to the chosen whitelisted
    slot, verified by loading the slot and reading its name. Stops at the
    first failure with what landed."""
    landed_cabs: list[dict] = []
    for c in pl.cabs:
        if c.kept:
            landed_cabs.append({"name": c.name, "slot": c.slot, "editor": c.editor,
                                "moved_from": None, "kept": True, "verified": True})
            continue
        res = fm9.install_user_cab_slot(c.raw, c.slot, c.file, expect_name=c.name)
        if not res.landed:
            raise GalleryInstallError(
                f"stopped at cab {c.name}: {res.note}. Landed so far: "
                + (", ".join(x["editor"] for x in landed_cabs) or "nothing"))
        landed_cabs.append({"name": c.name, "slot": c.slot, "editor": c.editor,
                            "moved_from": _label(c.wanted) if c.moved and c.wanted is not None else None,
                            "kept": False, "verified": res.verified})
    so_far = ", ".join(x["editor"] for x in landed_cabs) or "nothing"
    pf = fm9.load_preset_buffer(pl.preset_raw, pl.preset_name)
    changes = repoint(fm9, pl.moves)          # in the buffer, read back
    fm9.store_preset(pl.store_slot)           # the one flash write, whitelisted
    loaded = fm9.select_preset(pl.store_slot)
    read_back = loaded[1] if loaded else None
    if not read_back or p.is_empty_slot_name(read_back) or \
            read_back.strip() != pf.name.strip():
        raise GalleryInstallError(
            f"slot {p.slot_label(pl.store_slot)} loaded as {read_back!r}, not "
            f"{pf.name!r}; cabs landed at {so_far}")
    store_label = p.slot_label(pl.store_slot)
    return {"ok": True, "line": result_line(pl, store_label),
            "store_slot": pl.store_slot, "store_label": store_label,
            "preset": pf.name, "read_back": read_back, "cabs": landed_cabs,
            "repointed": changes, "notes": pl.notes, "replaced": pl.replaced}
