"""FM9 device transport and high-level control API.

Safety contract: this module NEVER stores/saves anything on the unit.
All writes hit the volatile edit buffer only. The editor protocol's
store command (fn 0x01 sub 0x26) is deliberately not implemented.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import mido

from . import protocol as p
from .registry import Registry, ParamSpec

RESULT_CODES = {
    0x00: "ok",
    0x05: "rejected (invalid function for this device)",
    0x08: "invalid effect id",
    0x09: "invalid param id",
}


class FM9NotFound(RuntimeError):
    pass


# The ONLY preset slots this tool is ever allowed to store to. Designate a
# small range of disposable test slots and keep everything else on the unit
# protected: store_preset refuses any slot outside this range.
SAFE_STORE_SLOTS = range(133, 141)


@dataclass
class SetResult:
    ok: bool
    detail: str
    display_before: float | str | None
    display_after: float | str | None


class FM9:
    def __init__(self, registry: Registry | None = None, port_hint: str = "fm9"):
        self.reg = registry or Registry()
        ins = [n for n in mido.get_input_names() if port_hint in n.lower()]
        outs = [n for n in mido.get_output_names() if port_hint in n.lower()]
        if not ins or not outs:
            raise FM9NotFound("FM9 MIDI ports not found; is it connected and powered on?")
        self.inp = mido.open_input(ins[0])
        self.outp = mido.open_output(outs[0])
        # per-effect channel info, refreshed from status dumps
        self._channels: dict[int, int] = {}
        self._current_channel: dict[int, int] = {}

    def close(self):
        self.inp.close()
        self.outp.close()

    # --- transport ---

    def _drain(self):
        for _ in self.inp.iter_pending():
            pass

    def _send(self, frame: list[int]):
        self.outp.send(mido.Message("sysex", data=frame[1:-1]))

    def _request(self, frame: list[int], want, timeout: float = 1.0):
        """Send a frame, return first inbound sysex for which want(data) is
        not None. Other frames (broadcasts) are ignored."""
        self._drain()
        self._send(frame)
        deadline = time.time() + timeout
        while time.time() < deadline:
            for msg in self.inp.iter_pending():
                if msg.type != "sysex":
                    continue
                data = list(msg.data)
                got = want(data)
                if got is not None:
                    return got
                nack = p.parse_multipurpose(data)
                if nack is not None and nack[1] != 0x00:
                    fn, code = nack
                    raise RuntimeError(
                        f"device rejected fn 0x{fn:02X}: "
                        f"{RESULT_CODES.get(code, f'result 0x{code:02X}')}")
            time.sleep(0.005)
        return None

    # --- official surface ---

    def firmware(self):
        return self._request(p.build_get_firmware(),
                             lambda d: d[5:7] if p.is_fractal(d, p.FN_FIRMWARE) else None)

    def current_preset(self) -> tuple[int, str] | None:
        return self._request(p.build_get_patch_name(), p.parse_patch_name)

    def current_scene(self) -> int | None:
        return self._request(p.build_get_scene(), p.parse_scene)

    def scene_name(self, scene: int | None = None) -> tuple[int, str] | None:
        return self._request(p.build_get_scene_name(scene), p.parse_scene_name)

    def set_scene(self, scene_1based: int) -> int | None:
        """Returns the scene the device reports after the change."""
        got = self._request(p.build_set_scene(scene_1based), p.parse_scene)
        if got is None:  # some responses race; confirm with a query
            got = self.current_scene()
        return got

    def status_dump(self):
        blocks = self._request(p.build_status_dump(), p.parse_status_dump, timeout=1.5)
        if blocks:
            for b in blocks:
                self._channels[b.effect_id] = max(1, b.channels_supported)
                self._current_channel[b.effect_id] = b.channel
        return blocks

    def get_bypass(self, effect_id: int) -> bool | None:
        got = self._request(p.build_get_bypass(effect_id),
                            lambda d: p.parse_bypass(d))
        return got[1] if got else None

    def set_bypass(self, effect_id: int, bypassed: bool) -> bool | None:
        got = self._request(p.build_set_bypass(effect_id, bypassed), p.parse_bypass)
        if got is None:
            got_b = self.get_bypass(effect_id)
            return got_b
        return got[1]

    def get_channel(self, effect_id: int) -> int | None:
        got = self._request(p.build_get_channel(effect_id), p.parse_channel)
        return got[1] if got else None

    def set_channel(self, effect_id: int, channel_0based: int) -> int | None:
        got = self._request(p.build_set_channel(effect_id, channel_0based), p.parse_channel)
        return got[1] if got else None

    def select_preset(self, preset: int) -> tuple[int, str] | None:
        """Preset switch via PC + CC0 bank (FM9 reads bank from CC0).
        Discards the edit buffer by loading the stored preset."""
        bank, pc = divmod(preset, 128)
        self.outp.send(mido.Message("control_change", control=0, value=bank))
        self.outp.send(mido.Message("program_change", program=pc))
        time.sleep(0.4)
        return self.current_preset()

    # --- editor protocol (community, fw 11.x) ---

    def _param_echo(self, frame: list[int], effect_id: int, param_id: int,
                    timeout: float = 1.0) -> p.ParamEcho | None:
        def want(d):
            echo = p.parse_param_echo(d)
            if echo and echo.effect_id == effect_id and echo.param_id == param_id:
                return echo
            return None
        return self._request(frame, want, timeout)

    def bulk_read(self, effect_id: int, timeout: float = 1.5) -> list[int] | None:
        """fn=0x1F whole-block read: returns positional wire16 values where
        index == device paramId (channel-blocked across the block's channels)."""
        self._drain()
        self._send(p.build_bulk_read_poll(effect_id))
        deadline = time.time() + timeout
        head = None
        values: list[int] = []
        while time.time() < deadline:
            for msg in self.inp.iter_pending():
                if msg.type != "sysex":
                    continue
                data = list(msg.data)
                h = p.parse_bcast_head(data)
                if h is not None:
                    if h[0] == effect_id:
                        head = h
                    continue
                if head is None:
                    continue
                body = p.parse_bcast_body(data)
                if body is not None:
                    values.extend(body)
                    continue
                if p.is_fractal(data, p.FN_BCAST_END):
                    return values if head and len(values) >= head[1] else values or None
            time.sleep(0.002)
        return values or None

    def read_grid(self, timeout: float = 2.0):
        """Live routing-grid read (fn 0x01 sub 0x2E). Returns GridCell list."""
        return self._request(p.build_request_grid_layout(), p.parse_grid_layout,
                             timeout)

    def place_block(self, row_1based: int, col_1based: int, effect_id: int):
        """Place a block (or 0 to clear) at a grid cell. Edit buffer only.
        Sends the cell-select first; without it the insert lands on the
        device's internal cursor instead of the target cell."""
        self._drain()
        self._send(p.build_select_grid_cell(row_1based, col_1based))
        time.sleep(0.05)
        self._send(p.build_set_grid_cell(row_1based, col_1based, effect_id))
        time.sleep(0.3)

    def send_preset_file(self, syx_bytes: bytes) -> bool:
        """Send a .syx preset dump (0x77/0x78/0x79 chain addressed to the
        edit buffer) to the device, paced like Fractal-Bot. Loads into the
        volatile edit buffer only; persisting needs store_preset."""
        msgs = []
        i = 0
        while i < len(syx_bytes):
            j = syx_bytes.find(b"\xf7", i)
            if syx_bytes[i] != 0xF0 or j == -1:
                raise ValueError("not a clean .syx sysex stream")
            msgs.append(list(syx_bytes[i:j + 1]))
            i = j + 1
        if not msgs or msgs[0][4] != 0x12:
            raise ValueError(
                f"not an FM9 preset (model byte 0x{msgs[0][4]:02x})" if msgs
                else "empty .syx file")
        self._drain()
        for m in msgs:
            self._send(m)
            time.sleep(0.06)
        time.sleep(1.0)
        return True

    def store_preset(self, slot: int):
        """Persist the working buffer to a preset slot. Whitelisted slots
        only (133-140); refuses everything else."""
        if slot not in SAFE_STORE_SLOTS:
            raise PermissionError(
                f"store to slot {slot} refused: only test slots "
                f"{SAFE_STORE_SLOTS.start}-{SAFE_STORE_SLOTS.stop - 1} are allowed")
        self._drain()
        self._send(p.build_store_preset(slot))
        time.sleep(1.5)
        return self.current_preset()

    def rename_preset(self, name: str):
        self._drain()
        self._send(p.build_rename_preset(name))
        time.sleep(0.2)

    def rename_scene(self, scene_1based: int, name: str):
        self._drain()
        self._send(p.build_set_scene_name(scene_1based - 1, name))
        time.sleep(0.2)

    def read_display_name(self, effect_id: int, param_id: int) -> str | None:
        """Read a param's display string via the type-name query (sub 0x1F)."""
        def want(d):
            got = p.parse_type_name_response(d)
            if got and got[0] == effect_id and got[1] == param_id:
                return got[2]
            return None
        return self._request(p.build_get_type_name(effect_id, param_id), want,
                             timeout=0.8)

    def mod_source_name(self, slot_1based: int) -> str | None:
        """Read the display name of a modifier slot's current source."""
        return self.read_display_name(p.mod_slot_eid(slot_1based), p.MOD_PID_SOURCE)

    def bind_modifier(self, slot_1based: int, target_effect_id: int,
                      target_param_id: int, source_ordinal: int,
                      min_norm: float = 0.0, max_norm: float = 1.0):
        """Bind a modifier slot: pedal/controller source -> block parameter.
        Edit buffer only.

        CRITICAL: a fresh (never-used) modifier slot has ALL fields zeroed,
        including the transfer curve (mid/end/slope/scale/offset). A zero
        curve maps every source position to zero, so the binding silently
        does nothing. This initializes the curve to linear defaults every
        time; callers may then override min/max for range floors."""
        eid = p.mod_slot_eid(slot_1based)
        for pid, val in ((p.MOD_PID_TARGET_EFFECT, target_effect_id),
                         (p.MOD_PID_TARGET_PARAM, target_param_id),
                         (p.MOD_PID_SOURCE, source_ordinal)):
            self._drain()
            self._send(p.build_set_param_discrete(eid, pid, val))
            time.sleep(0.15)
        # linear transfer curve: start 0, mid 0.5, end 1.0, slope/scale/offset
        # centered, plus caller's range. Field pids per forgefx-midi map.
        curve = ((1, min_norm), (2, max_norm), (3, 0.0), (4, 0.5), (5, 1.0),
                 (6, 0.5), (13, 0.5), (14, 0.5))
        for pid, val in curve:
            self._drain()
            self._send(p.build_set_param_continuous(eid, pid, val))
            time.sleep(0.08)

    def connect_cells(self, src_row: int, src_col: int, dest_row: int,
                      disconnect: bool = False):
        op = p.ROUTING_DISCONNECT if disconnect else p.ROUTING_CONNECT
        self._drain()
        self._send(p.build_set_grid_routing(src_row, src_col, dest_row, op))
        time.sleep(0.25)

    def get_param_wire(self, spec: ParamSpec, channel: int | None = None) -> int | None:
        """Read one param's wire16 value via bulk read. `channel` 0..3 picks the
        channel copy; defaults to the block's current channel."""
        values = self.bulk_read(spec.effect_id)
        if not values:
            return None
        chans = max(1, self._channels.get(spec.effect_id, 1))
        stride = len(values) // chans if chans > 1 else len(values)
        if channel is None:
            channel = self._current_channel.get(spec.effect_id, 0)
        idx = min(channel, chans - 1) * stride + spec.param_id
        if idx >= len(values):
            idx = spec.param_id
        return values[idx] if idx < len(values) else None

    def get_param_display(self, spec: ParamSpec) -> float | str | None:
        wire = self.get_param_wire(spec)
        if wire is None:
            return None
        if spec.kind == "enum":
            return wire
        if spec.dmin is None or spec.dmax is None:
            return wire / 65534
        return round(p.normalized_to_display(wire / 65534, spec.dmin, spec.dmax,
                                             spec.scale), 2)

    def set_param_display(self, spec: ParamSpec, display_value: float) -> SetResult:
        """Set a continuous param by display value, with read-back verify."""
        if spec.dmin is None or spec.dmax is None:
            return SetResult(False, f"{spec.name} has no calibrated range", None, None)
        before = self.get_param_display(spec)
        normalized = p.display_to_normalized(display_value, spec.dmin, spec.dmax, spec.scale)
        frame = p.build_set_param_continuous(spec.effect_id, spec.param_id, normalized)
        self._param_echo(frame, spec.effect_id, spec.param_id, timeout=0.3)
        # The device applies the write asynchronously; a bulk read fired too
        # soon returns the pre-write value. Settle, then verify with retries.
        target = min(spec.dmax, max(spec.dmin, display_value))
        quantum = (spec.dmax - spec.dmin) / 65534 * 2 + 1e-6
        tol = max(quantum, 0.02)
        after = None
        ok = False
        for _ in range(4):
            time.sleep(0.15)
            after = self.get_param_display(spec)
            if isinstance(after, (int, float)) and abs(after - target) <= tol:
                ok = True
                break
        return SetResult(ok, "verified by read-back" if ok else f"read-back mismatch: {after}",
                         before, after)

    def set_param_ordinal(self, spec: ParamSpec, ordinal: int) -> SetResult:
        """Set a discrete (enum/type) param by roster ordinal."""
        before = self.get_param_display(spec)
        frame = p.build_set_param_discrete(spec.effect_id, spec.param_id, ordinal)
        self._param_echo(frame, spec.effect_id, spec.param_id, timeout=0.6)
        after = self.get_param_display(spec)
        return SetResult(True, "sent (discrete)", before, after)
