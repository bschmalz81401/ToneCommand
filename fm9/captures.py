"""#162: the capture-slot primitive's in-memory store, and the whitelist
that gates it.

The FM9 has no capture protocol yet (Epic I waits on firmware, I0), so
there are no frames to simulate. What Epic I builds against is the
primitive's contract, and this store IS the sim's implementation of it:
slots, occupancy, the I1 record, a whitelist the owner designates, and
references from stored presets. A real device backend (I4 for Fractal, K2
for the ToneX) replaces the store, not the contract.

Refusal order for install, each a one-line ValueError naming the slot:
(1) outside the whitelist, (2) occupied and referenced by a stored preset
(the preset is named). An occupied, unreferenced slot is overwritten. Full
is not a refusal here; eviction is I4's. remove refuses only a referenced
slot; removing an empty slot is a no-op.
"""
from __future__ import annotations

import os
from pathlib import Path

from fm9.adapter import CaptureCapabilities, CaptureInstall, CaptureSlot


def get_nam_slots() -> set[int]:
    """The ONLY capture slots this tool may write into: TONECOMMAND_NAM_SLOTS,
    env or .env, 0-based, ranges and commas like TONECOMMAND_CAB_SLOTS.
    DEFAULT IS EMPTY: capture installs are disabled until the owner
    designates slots, the same rule as user cabs and preset stores."""
    raw = os.environ.get("TONECOMMAND_NAM_SLOTS", "").strip()
    if not raw:
        env_file = Path(__file__).resolve().parent.parent / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("TONECOMMAND_NAM_SLOTS="):
                    raw = line.split("=", 1)[1].strip()
                    break
    slots: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                slots.update(range(int(a), int(b) + 1))
            except ValueError:
                pass
        else:
            try:
                slots.add(int(part))
            except ValueError:
                pass
    return {n for n in slots if 0 <= n <= 1023}


class CaptureStore:
    """Slots in memory, whitelist-gated, reference-aware, read back after
    every write. Formats and slot count are the device's to declare."""

    def __init__(self, slots: int = 8, formats: tuple = (".nam",), whitelist=None):
        self.slot_count = int(slots)
        self.formats = tuple(formats)
        self._whitelist = whitelist   # None: read the env each time (tests set it per test)
        self._contents: dict[int, tuple] = {}          # slot -> (record, raw)
        self._references: dict[int, set[int]] = {}    # slot -> preset numbers

    # -- the four operations --------------------------------------------------

    def capture_capabilities(self) -> CaptureCapabilities:
        return CaptureCapabilities(self.formats, self.slot_count, frozenset(self.whitelist()))

    def list_captures(self) -> list:
        rows = []
        for slot in range(self.slot_count):
            held = self._contents.get(slot)
            record = held[0] if held else None
            name = getattr(record, "name", None) if held else None
            rows.append(CaptureSlot(slot, held is not None, name, record))
        return rows

    def install_capture(self, record, raw: bytes, slot: int) -> CaptureInstall:
        self._check_slot(slot)
        if slot not in self.whitelist():
            raise ValueError(f"capture install to slot {slot} refused: it is outside "
                             "TONECOMMAND_NAM_SLOTS")
        holders = self.references_of(slot)
        if slot in self._contents and holders:
            raise ValueError(f"capture install to slot {slot} refused: the capture there is "
                             f"used by stored preset {self._name_presets(holders)}")
        _before = self._contents.get(slot)                # read before write (occupancy)
        self._contents[slot] = (record, bytes(raw))
        after = self._contents[slot]                     # read back after
        verified = after[1] == bytes(raw)
        return CaptureInstall(slot, verified, "read back byte for byte" if verified else "read-back mismatch")

    def remove_capture(self, slot: int):
        self._check_slot(slot)
        holders = self.references_of(slot)
        if holders:
            raise ValueError(f"capture removal from slot {slot} refused: it is used by stored "
                             f"preset {self._name_presets(holders)}")
        return self._contents.pop(slot, None) is not None

    # -- references, explicit and never parsed from names ---------------------

    def reference_capture(self, slot: int, preset_number: int) -> None:
        self._references.setdefault(int(slot), set()).add(int(preset_number))

    def unreference_capture(self, slot: int, preset_number: int) -> None:
        self._references.get(int(slot), set()).discard(int(preset_number))

    def unreference_preset(self, preset_number: int) -> None:
        """A preset re-stored without a capture drops every reference it held."""
        for holders in self._references.values():
            holders.discard(int(preset_number))

    def references_of(self, slot: int) -> set[int]:
        return set(self._references.get(int(slot), set()))

    # -- helpers --------------------------------------------------------------

    def whitelist(self) -> set[int]:
        return set(self._whitelist) if self._whitelist is not None else get_nam_slots()

    def _check_slot(self, slot: int) -> None:
        if not isinstance(slot, int) or slot < 0 or slot >= self.slot_count:
            raise ValueError(f"capture slot {slot} does not exist on this device "
                             f"(0 to {self.slot_count - 1})")

    @staticmethod
    def _name_presets(holders: set[int]) -> str:
        return ", ".join(str(n) for n in sorted(holders))


class NoCaptures:
    """The honest answer of a device that plays no captures (I7's
    no-support path): capabilities say so, list is empty, and the two
    writes refuse with one line. Shared by the FM9 (until Epic I lands)
    and the HeadRush adapter."""

    def capture_capabilities(self) -> CaptureCapabilities:
        return CaptureCapabilities((), 0, frozenset())

    def list_captures(self) -> list:
        return []

    def install_capture(self, record, raw: bytes, slot: int) -> CaptureInstall:
        from fm9.adapter import NO_CAPTURES
        raise NotImplementedError(NO_CAPTURES)

    def remove_capture(self, slot: int):
        from fm9.adapter import NO_CAPTURES
        raise NotImplementedError(NO_CAPTURES)
