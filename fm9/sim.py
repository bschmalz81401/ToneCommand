"""Virtual FM9: a frame-level simulator for hardware-free testing.

Speaks the same SysEx surface as fm9/protocol.py against in-memory state,
faithfully modeling the quirks learned on real hardware (fw 11.00):

- grid insert without a preceding cell-select lands on the internal cursor
- placing an already-placed block at another cell is IGNORED
- clearing a grid cell destroys its cables
- fresh modifier slots are all-zero (a bind without curve init is dead)
- sub 09 00 with value 0 on a continuous param is a no-op (the zeroed GET)
- display-name reads (sub 1F) are fresh only for the AMP block
- bulk reads are channel-blocked

HARD RULE: this is a REGRESSION tool, never verification. New protocol
claims are proven on hardware first, then taught to the sim. A sim pass
says nothing about the real device.

Usage:
    from fm9.sim import SimFM9
    fm9 = SimFM9()            # an FM9 instance backed by the simulator
"""
from __future__ import annotations

import copy
import struct
from types import SimpleNamespace

from . import protocol as p
from .device import FM9
from .registry import Registry, EFFECT_ID_BASE

GRID_ROWS, GRID_COLS = 6, 14
STALE_NAME = "Quad-Tap Delay"   # the constant returned by non-amp 1F reads


def _f32_from_septets(s):
    bits = 0
    for i, b in enumerate(s[:5]):
        bits |= (b & 0x7F) << (7 * i)
    return struct.unpack("<f", struct.pack("<I", bits & 0xFFFFFFFF))[0]


class _Cell:
    def __init__(self, effect_id=None, is_shunt=False):
        self.effect_id = effect_id
        self.is_shunt = is_shunt
        self.cable_in_mask = 0   # bit n = fed from display row n of prev col


def _default_preset(number: int, reg: Registry) -> dict:
    """A minimal but realistic preset: In -> Amp -> Cab -> Delay -> Out on
    display row 2, with shunts, plus 8 scenes of per-block state."""
    grid = {}
    chain = [(2, 1, 37), (2, 2, None), (2, 3, 58), (2, 4, 62), (2, 5, None),
             (2, 6, 70), (2, 7, None), (2, 8, 42)]
    prev_row = None
    for row, col, eid in chain:
        c = _Cell(eid if eid else None, is_shunt=eid is None)
        if col > 1:
            c.cable_in_mask = 1 << 2   # fed from display row 2 of prev col
        grid[(row, col)] = c
    blocks = sorted({c.effect_id for c in grid.values() if c.effect_id})
    scenes = {s: {eid: {"bypassed": False, "channel": 0} for eid in blocks}
              for s in range(1, 9)}
    params = {}
    for eid in blocks:
        fam_inst = None
        for fam, (base, count) in EFFECT_ID_BASE.items():
            if base <= eid < base + count:
                fam_inst = fam
                break
        n_params = 1 + max((pid for (f, pid) in reg.params if f == fam_inst),
                           default=0)
        chans = 4 if eid not in (37, 42) else 1
        params[eid] = [[0] * n_params for _ in range(chans)]
    return {
        "number": number,
        "name": f"Sim Preset {number}",
        "scene_names": {s: f"Scene {s}" for s in range(1, 9)},
        "grid": grid,
        "scenes": scenes,
        "params": params,                     # per block: [channel][pid] wire16
        "modifiers": {s: [0] * 25 for s in range(1, 33)},   # all-zero slots!
        "tempo": 120,
    }


class SimState:
    def __init__(self, reg: Registry | None = None):
        self.reg = reg or Registry()
        self.presets: dict[int, dict] = {}
        self.buffer = self._load(0)
        self.scene = 1
        self.cursor = 0          # internal grid cursor (cell index)
        self.selected = False    # was a cell-select received since last insert

    def _load(self, number: int) -> dict:
        if number not in self.presets:
            self.presets[number] = _default_preset(number, self.reg)
        return copy.deepcopy(self.presets[number])

    def select_preset(self, number: int):
        self.buffer = self._load(number)     # discards edit buffer
        self.scene = 1

    # -- helpers --
    def block_param(self, eid, pid, ch=None):
        ch = self.buffer["scenes"][self.scene].get(eid, {}).get("channel", 0) if ch is None else ch
        rows = self.buffer["params"].get(eid)
        if rows is None or pid >= len(rows[0]):
            return None
        return rows[min(ch, len(rows) - 1)][pid]

    def set_block_param(self, eid, pid, wire, ch=None):
        ch = self.buffer["scenes"][self.scene].get(eid, {}).get("channel", 0) if ch is None else ch
        rows = self.buffer["params"].get(eid)
        if rows is not None and pid < len(rows[0]):
            rows[min(ch, len(rows) - 1)][pid] = max(0, min(65534, int(wire)))


class SimFM9Core:
    """Consumes protocol frames, mutates SimState, emits response frames."""

    def __init__(self, state: SimState | None = None):
        self.st = state or SimState()

    def handle(self, frame: list[int]) -> list[list[int]]:
        d = frame[1:-1]                        # strip F0/F7 -> mido-style data
        if list(d[:3]) != list(p.MFR) or d[3] != p.MODEL_FM9:
            return []
        fn = d[4]
        body = d[5:-1]                          # strip checksum
        h = getattr(self, f"_fn_{fn:02x}", None)
        return h(body) if h else []

    # ---- official surface ----
    def _fn_0c(self, b):
        if b and b[0] != 0x7F:
            self.st.scene = b[0] + 1
        return [p.envelope(p.FN_SCENE, [self.st.scene - 1])]

    def _fn_0d(self, b):
        if len(b) >= 2 and not (b[0] == 0x7F and b[1] == 0x7F):
            num = p.decode14(b[0], b[1])
            name = self.st.presets.get(num, {}).get("name") or \
                _default_preset(num, self.st.reg)["name"]
        else:
            num, name = self.st.buffer["number"], self.st.buffer["name"]
        payload = [*p.encode14(num)] + [ord(c) for c in name.ljust(32)[:32]]
        return [p.envelope(p.FN_PATCH_NAME, payload)]

    def _fn_0e(self, b):
        s = self.st.scene if (not b or b[0] == 0x7F) else b[0] + 1
        name = self.st.buffer["scene_names"].get(s, f"Scene {s}")
        return [p.envelope(p.FN_SCENE_NAME, [s - 1] + [ord(c) for c in name.ljust(32)[:32]])]

    def _fn_0a(self, b):
        eid = p.decode14(b[0], b[1])
        st = self.st.buffer["scenes"][self.st.scene].setdefault(
            eid, {"bypassed": False, "channel": 0})
        if b[2] != 0x7F:
            st["bypassed"] = bool(b[2])
        return [p.envelope(p.FN_BYPASS, [*p.encode14(eid), 1 if st["bypassed"] else 0])]

    def _fn_0b(self, b):
        eid = p.decode14(b[0], b[1])
        st = self.st.buffer["scenes"][self.st.scene].setdefault(
            eid, {"bypassed": False, "channel": 0})
        if b[2] != 0x7F:
            st["channel"] = b[2]
        return [p.envelope(p.FN_CHANNEL, [*p.encode14(eid), st["channel"]])]

    def _fn_13(self, b):
        out = []
        for eid, st in sorted(self.st.buffer["scenes"][self.st.scene].items()):
            chans = len(self.st.buffer["params"].get(eid, [[0]]))
            dd = (1 if st["bypassed"] else 0) | (st["channel"] << 1) | (chans << 4)
            out += [*p.encode14(eid), dd]
        return [p.envelope(p.FN_STATUS_DUMP, out)]

    def _fn_14(self, b):
        if len(b) >= 2 and not (b[0] == 0x7F and b[1] == 0x7F):
            self.st.buffer["tempo"] = p.decode14(b[0], b[1])
        return [p.envelope(p.FN_TEMPO_BPM, list(p.encode14(self.st.buffer["tempo"])))]

    def _fn_08(self, b):
        return [p.envelope(p.FN_FIRMWARE, [11, 0, 0, 1])]

    # ---- bulk read ----
    def _fn_1f(self, b):
        eid = p.decode14(b[0], b[1])
        if 3 <= eid <= 34:                       # modifier slots are readable
            flat = list(self.st.buffer["modifiers"][eid - 2])
        else:
            rows = self.st.buffer["params"].get(eid)
            if rows is None:
                return [p.envelope(p.FN_MULTIPURPOSE, [0x1F, 0x08, 0x00])]
            flat = [v for row in rows for v in row]
        frames = [p.envelope(p.FN_BCAST_HEAD, [*p.encode14(eid), *p.encode14(len(flat))])]
        CHUNK = 128
        for off in range(0, len(flat), CHUNK):
            payload = [0x00, 0x00]
            for v in flat[off:off + CHUNK]:
                payload += [v & 0x7F, (v >> 7) & 0x7F, (v >> 14) & 0x03]
            frames.append(p.envelope(p.FN_BCAST_BODY, payload))
        frames.append(p.envelope(p.FN_BCAST_END, []))
        return frames

    # ---- editor protocol ----
    def _fn_01(self, b):
        sub = (b[0], b[1])
        if sub == (0x09, 0x00):
            return self._set_discrete(b)
        if sub == (0x52, 0x00):
            return self._set_continuous(b)
        if sub == (0x1F, 0x00):
            return self._type_name(b)
        if sub == (0x30, 0x00):
            self.st.cursor = b[6] | (b[7] << 7)   # raw uint32 low septets
            self.st.selected = True
            return []
        if sub == (0x32, 0x00):
            return self._grid_insert(b)
        if sub == (0x35, 0x00):
            return self._cable(b)
        if sub == (0x2E, 0x00):
            return self._grid_read(b)
        if sub == (0x28, 0x00):
            self.st.buffer["name"] = self._unpack_name(b)
            return []
        if sub == (0x2B, 0x00):
            self.st.buffer["scene_names"][b[4] + 1] = self._unpack_name(b)
            return []
        if sub == (0x26, 0x00):
            slot = p.decode14(b[6], b[7])
            snap = copy.deepcopy(self.st.buffer)
            snap["number"] = slot
            self.st.presets[slot] = snap
            self.st.buffer = copy.deepcopy(snap)
            return []
        return []

    def _target(self, b):
        return p.decode14(b[2], b[3]), p.decode14(b[4], b[5])

    def _set_discrete(self, b):
        eid, pid = self._target(b)
        val = _f32_from_septets(b[6:11])
        if 3 <= eid <= 34:                       # modifier slot params
            slots = self.st.buffer["modifiers"]
            slots[eid - 2][pid] = int(round(val)) if pid < 25 else 0
            return []
        spec_kind = self._param_kind(eid, pid)
        if spec_kind == "enum":
            self.st.set_block_param(eid, pid, int(round(val)))
        # continuous + value 0.0 => the zeroed GET: a NO-OP (hardware-observed)
        return [self._echo(eid, pid)]

    def _set_continuous(self, b):
        eid, pid = self._target(b)
        norm = max(0.0, min(1.0, _f32_from_septets(b[6:11])))
        if 3 <= eid <= 34:
            self.st.buffer["modifiers"][eid - 2][pid] = int(round(norm * 65534))
            return []
        self.st.set_block_param(eid, pid, round(norm * 65534))
        return [self._echo(eid, pid)]

    def _param_kind(self, eid, pid):
        fam = self.st.reg.family_of_effect_id(eid)
        if not fam:
            return "unknown"
        return self.st.reg.spec(fam[0], pid, fam[1]).kind

    def _echo(self, eid, pid):
        wire = self.st.block_param(eid, pid) or 0
        norm = wire / 65534
        bits = struct.unpack("<I", struct.pack("<f", norm))[0]
        sept = [(bits >> (7 * i)) & 0x7F for i in range(5)]
        return p.envelope(p.FN_PARAM, [0x09, 0x00, *p.encode14(eid),
                                       *p.encode14(pid), *sept, 0, 0, 0, 0])

    def _type_name(self, b):
        eid, pid = self._target(b)
        if 58 <= eid <= 61:                      # amp: fresh names (hardware)
            w = self.st.block_param(eid, 10) or 0
            name = self.st.reg.amp_roster.get(str(w), "Unknown")
        elif 3 <= eid <= 34:
            name = "NONE"                        # mod sources: stale NONE
        else:
            name = STALE_NAME                    # everything else: stale
        raw = name.encode("ascii", "replace")
        payload = [0x1F, 0x00, *p.encode14(eid), *p.encode14(pid),
                   0, 0, 0, 0, 0, 0, 0, *p.encode14(len(raw)),
                   *p.pack_chunked(raw)]
        return [p.envelope(p.FN_PARAM, payload)]

    def _grid_insert(self, b):
        block_id = p.decode14(b[2], b[3])
        grid_pos = p.decode14(b[6], b[7])
        # QUIRK: without a preceding select, insert lands on the cursor cell
        pos = grid_pos if self.st.selected else self.st.cursor
        self.st.selected = False
        col, row = divmod(pos, GRID_ROWS)
        key = (row + 1, col + 1)
        grid = self.st.buffer["grid"]
        if block_id == 0:
            grid.pop(key, None)                  # QUIRK: clear kills cables too
            return []
        # QUIRK: placing an already-placed block elsewhere is IGNORED
        placed = {c.effect_id for c in grid.values() if c.effect_id}
        if block_id in placed:
            return []
        cell = grid.get(key) or _Cell()
        cell.effect_id, cell.is_shunt = block_id, False
        grid[key] = cell
        if block_id not in self.st.buffer["params"]:
            fam = self.st.reg.family_of_effect_id(block_id)
            n = 1 + max((pid for (f, pid) in self.st.reg.params if fam and f == fam[0]), default=0)
            self.st.buffer["params"][block_id] = [[0] * n for _ in range(4)]
        self.st.buffer["scenes"][self.st.scene].setdefault(
            block_id, {"bypassed": False, "channel": 0})
        for s in range(1, 9):
            self.st.buffer["scenes"][s].setdefault(
                block_id, {"bypassed": False, "channel": 0})
        return []

    _CABLE_LUT = None

    @classmethod
    def _cable_lut(cls):
        if cls._CABLE_LUT is None:
            lut = {}
            for sc in range(1, 14):
                for sr in range(1, 7):
                    if sr == 1 and sc % 2 == 0:
                        continue
                    for dr in range(1, 7):
                        f = p.build_set_grid_routing(sr, sc, dr)
                        lut[tuple(f[1:-1][5:][15:18])] = (sr, sc, dr)
            cls._CABLE_LUT = lut
        return cls._CABLE_LUT

    def _cable(self, b):
        op = b[6]
        key = tuple(b[15:18])
        hit = self._cable_lut().get(key)
        if not hit:
            return [p.envelope(p.FN_MULTIPURPOSE, [0x01, 0x16, 0x00])]
        sr, sc, dr = hit
        cell = self.st.buffer["grid"].setdefault((dr, sc + 1), _Cell(is_shunt=False))
        if op == p.ROUTING_CONNECT:
            cell.cable_in_mask |= (1 << sr)
        else:
            cell.cable_in_mask &= ~(1 << sr)
        return []

    def _grid_read(self, b):
        base_bit, row_stride = 46, 32
        region_bytes = -(-(base_bit + GRID_COLS * GRID_ROWS * row_stride) // 7)
        region = [0] * region_bytes

        def put(bit, n, val):
            for i in range(n):
                if (val >> (n - 1 - i)) & 1:
                    region[(bit + i) // 7] |= 1 << (6 - ((bit + i) % 7))

        for (row1, col1), cell in self.st.buffer["grid"].items():
            base = base_bit + (col1 - 1) * GRID_ROWS * row_stride + (row1 - 1) * row_stride
            ident = 0x08 if cell.is_shunt else ((cell.effect_id or 0) & 0x7F)
            put(base, 8, (ident << 1) & 0xFF)
            put(base + 8, 8, 0x08 if cell.is_shunt else 0x00)
            put(base + 16, 8, cell.cable_in_mask & 0xFF)
        payload = [0x2E, 0x00] + [0] * 354 + region
        return [p.envelope(p.FN_PARAM, payload)]

    def _unpack_name(self, b):
        return p.unpack_chunked(list(b[15:]), 32).decode("ascii", "replace").rstrip("\x00 ")


class _SimIn:
    def __init__(self):
        self.queue = []

    def iter_pending(self):
        q, self.queue = self.queue, []
        yield from q

    def close(self):
        pass


class _SimOut:
    def __init__(self, core: SimFM9Core, inp: _SimIn):
        self.core, self.inp = core, inp

    def send(self, msg):
        if msg.type == "sysex":
            frame = [0xF0, *msg.data, 0xF7]
            for resp in self.core.handle(frame):
                self.inp.queue.append(SimpleNamespace(type="sysex", data=resp[1:-1]))
        elif msg.type == "program_change":
            bank = getattr(self.core, "_bank", 0)
            self.core.st.select_preset(bank * 128 + msg.program)
        elif msg.type == "control_change" and msg.control == 0:
            self.core._bank = msg.value

    def close(self):
        pass


def SimFM9(registry: Registry | None = None) -> FM9:
    """An FM9 device instance backed by the simulator."""
    core = SimFM9Core(SimState(registry))
    inp = _SimIn()
    outp = _SimOut(core, inp)
    dev = FM9(registry=core.st.reg, ports=(inp, outp))
    dev.sim_core = core   # exposed for test assertions
    return dev
