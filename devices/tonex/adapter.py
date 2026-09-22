"""The ToneX pedal on K1's capture primitive, read-only (issue #163, K2).

What the pedal reports is the surface: on every program change it emits a
full preset dump (type 0x0204) over its CDC serial port, and that frame
carries the preset's name (string slot 0) and category (slot 3). The MIDI
bank/footswitch map is PC = bank * 3 + switch (verified on hardware: PC 78
lit bank 26; kb/TONEX_PROTOCOL.md). That is enough for the two things this
adapter does: say what it can take and list what is loaded.

What it does NOT do, by construction: write. The capture-upload path is
undecoded (#27) and stays behind invariant 0 (never brick), so install and
remove refuse in one line before any frame exists, and the adapter has no
transport method that sends. Frames come in through a SOURCE, a callable
returning {pc: raw frame bytes}: recorded frames in tests, the pedal's own
dumps when it is plugged in.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

from fm9.adapter import (Capabilities, CaptureCapabilities, CaptureSlot,
                         ReadPath, Topology)

from . import frames as fr

PROGRAMS = 128
SWITCHES = ("A", "B", "C")
FORMATS = (".tmodel",)

#: Refusal, one line. Named for the issue that owns the upload path and the
#: rule that keeps it closed until that path is decoded and proven.
INSTALL_REFUSED = ("the ToneX capture-upload path is not decoded yet (#27) and "
                   "stays closed under invariant 0, never brick; nothing was "
                   "sent. Load captures with the TONEX Editor for now")

FIXTURE_DIR = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures"
LOCAL_CAPTURES = (Path(__file__).resolve().parent.parent.parent / "kb"
                  / "tone_library" / "tonex_captures")


def program_of(bank: int, switch: str | int) -> int:
    """Bank N, footswitch A/B/C -> Program Change. Zero-based banks."""
    idx = SWITCHES.index(switch.upper()) if isinstance(switch, str) else int(switch)
    if not 0 <= idx <= 2:
        raise ValueError("footswitch is A, B or C")
    pc = int(bank) * 3 + idx
    if not 0 <= pc < PROGRAMS:
        raise ValueError(f"bank {bank} {SWITCHES[idx]} is PC {pc}, off the pedal")
    return pc


def bank_of(pc: int) -> tuple[int, str]:
    """Program Change -> (bank, footswitch letter)."""
    if not 0 <= int(pc) < PROGRAMS:
        raise ValueError(f"PC {pc} is off the pedal (0..{PROGRAMS - 1})")
    return int(pc) // 3, SWITCHES[int(pc) % 3]


class NotSupported(RuntimeError):
    """A contract method with no ToneX meaning. Stated, not faked."""


class ReadOnly(PermissionError):
    """A write the adapter refuses before any transport (#163)."""


class RecordedFrames:
    """A frame source over files: the local recorded set when present, else
    the committed fixture (names and categories for all 128, raw bytes for
    two). Never opens a port."""

    def __init__(self, directory: Path | None = None):
        self.directory = directory

    def __call__(self) -> dict[int, bytes]:
        d = self.directory or LOCAL_CAPTURES
        out: dict[int, bytes] = {}
        if d.exists():
            for pc in range(PROGRAMS):
                p = d / f"pc{pc:03d}.bin"
                if p.exists():
                    out[pc] = p.read_bytes()
        return out


class FixtureFrames:
    """The committed fixture as a source: decoded names and categories for
    every program, raw frames where the fixture carries them."""

    def __init__(self, path: Path | None = None):
        self.path = path or FIXTURE_DIR / "tonex_presets.json"

    def __call__(self) -> dict[int, bytes]:
        out: dict[int, bytes] = {}
        for pc in range(PROGRAMS):
            p = FIXTURE_DIR / f"tonex_pc{pc:03d}.bin"
            if p.exists():
                out[pc] = p.read_bytes()
        return out

    def programs(self) -> list[dict]:
        return json.loads(self.path.read_text(encoding="utf-8"))["programs"]


class ToneXAdapter:
    """K1's four capture operations for the pedal, and nothing that writes."""

    CAPABILITIES = Capabilities(
        read_path=ReadPath.DEVICE,
        observes_foreign_writes=True,     # the pedal announces every change
        reads_slot_names=True,
        reads_slot_state=False,
        verifies_writes=False,            # there are no writes
        has_scenes=False,
        stores_presets=False,
        topology=Topology.FIXED,          # the pedal's chain is what it is
        has_modifiers=False,
        installs_files=False,
        can_rename=False,
        composable_scene_slots=False,
        plays_captures=True,
    )

    def __init__(self, frames: Callable[[], dict[int, bytes]] | None = None,
                 names: list[dict] | None = None):
        """`frames` yields {pc: raw dump}; `names` is an optional decoded
        table ({pc, name, category}) for programs the source has no raw
        frame for (the committed fixture carries names for all 128 and raw
        bytes for two). A raw frame always wins over the table."""
        self._frames = frames or RecordedFrames()
        self._names = {int(r["pc"]): r for r in (names or [])}
        self.undecoded: set[str] = set()
        self.undecoded.add(
            "ToneX: amp identity is not in the preset frame (the tone lives in "
            "the capture); the name is the only handle. Install path #27.")

    # --- identity ---------------------------------------------------------

    def capabilities(self) -> Capabilities:
        return self.CAPABILITIES

    def capture_capabilities(self) -> CaptureCapabilities:
        # an empty whitelist: nothing may be written, by declaration as well
        # as by construction
        return CaptureCapabilities(FORMATS, PROGRAMS, frozenset())

    def firmware_label(self) -> str:
        return ""                          # not on the verified surface yet

    def evidence(self) -> dict:
        return {"surface": "0x0204 preset dumps over the CDC serial port; "
                           "PC = bank * 3 + switch (kb/TONEX_PROTOCOL.md)",
                "write_path": "none (#27, invariant 0)"}

    # --- the list ---------------------------------------------------------

    def programs(self) -> list[dict]:
        """{pc, bank, switch, name, category, decoded} for PC 0..127."""
        raw = self._frames()
        rows = []
        for pc in range(PROGRAMS):
            bank, switch = bank_of(pc)
            if pc in raw:
                f = fr.decode(raw[pc])
                if f.crc_ok is False:
                    self.undecoded.add(f"ToneX PC {pc}: frame failed its FCS")
                rows.append({"pc": pc, "bank": bank, "switch": switch,
                             "name": f.name, "category": f.category,
                             "decoded": True})
            elif pc in self._names:
                r = self._names[pc]
                rows.append({"pc": pc, "bank": bank, "switch": switch,
                             "name": str(r.get("name") or ""),
                             "category": str(r.get("category") or ""),
                             "decoded": False})
            else:
                rows.append({"pc": pc, "bank": bank, "switch": switch,
                             "name": "", "category": "", "decoded": False})
        return rows

    def list_captures(self) -> list:
        return [CaptureSlot(slot=r["pc"], occupied=bool(r["name"]),
                            name=r["name"] or None, record=None)
                for r in self.programs()]

    # --- no writes, stated -------------------------------------------------

    def install_capture(self, record: Any, raw: bytes, slot: int):
        raise NotImplementedError(INSTALL_REFUSED)

    def remove_capture(self, slot: int):
        raise NotImplementedError(INSTALL_REFUSED)

    # --- the rest of the DeviceAdapter surface ---------------------------
    # Reads answer from the frames; every write refuses in one line before
    # any transport exists to refuse through. Stated, not faked (as the
    # HeadRush adapter does for what has no meaning there).

    def status_dump(self) -> dict:
        rows = self.programs()
        return {"device": "tonex", "programs": len(rows),
                "decoded": sum(1 for r in rows if r["decoded"]),
                "current": None}          # the dump does not say which is loaded

    def current_preset(self) -> tuple:
        """(None, ''): a dump does not carry its own program number, and
        the adapter does not invent one."""
        return None, ""

    def slot_name(self, preset: int) -> str:
        return self.programs()[int(preset)]["name"]

    def is_slot_empty(self, preset: int) -> bool:
        return not self.programs()[int(preset)]["name"]

    def scan_slots(self, start: int = 0, end: int = PROGRAMS - 1) -> list:
        rows = self.programs()
        return [(r["pc"], r["name"]) for r in rows
                if int(start) <= r["pc"] <= min(int(end), PROGRAMS - 1)]

    def scene_name(self, scene: Any = None):
        return None                        # no scenes on this pedal

    def bulk_read(self, effect_id: int, timeout: float = 1.5):
        raise NotSupported("the ToneX exposes no block parameters to read; "
                           "its surface is captures (#163)")

    def get_param_display(self, spec: Any):
        raise NotSupported("the ToneX exposes no block parameters to read; "
                           "its surface is captures (#163)")

    def close(self) -> None:
        return None                        # nothing is held open

    def _refuse_write(self, what: str):
        raise ReadOnly(f"{what} refused: the ToneX adapter is read-only "
                       "(#163); nothing was sent")

    def select_preset(self, preset: int):
        self._refuse_write("select preset")

    def set_scene(self, scene_1based: int):
        self._refuse_write("set scene")

    def set_bypass(self, effect_id: int, bypassed: bool):
        self._refuse_write("set bypass")

    def set_channel(self, effect_id: int, channel_0based: int):
        self._refuse_write("set channel")

    def set_param_display(self, spec: Any, display_value: float):
        self._refuse_write("set parameter")

    def set_param_ordinal(self, spec: Any, ordinal: int):
        self._refuse_write("set parameter")

    def set_params_batch(self, items: Any):
        self._refuse_write("set parameters")

    def store_preset(self, slot: int):
        self._refuse_write("store preset")


def from_environment() -> ToneXAdapter:
    """The adapter the app builds: recorded frames plus the fixture names
    (TONECOMMAND_TONEX_SIM=1), or the live pedal behind
    TONECOMMAND_TONEX_PORT, which is read-only and not exercised in tests."""
    port = os.environ.get("TONECOMMAND_TONEX_PORT", "").strip()
    if port:
        from .serial_source import LiveFrames
        return ToneXAdapter(frames=LiveFrames(port))
    fx = FixtureFrames()
    recorded = RecordedFrames()
    frames = recorded if LOCAL_CAPTURES.exists() else fx
    return ToneXAdapter(frames=frames, names=fx.programs())
