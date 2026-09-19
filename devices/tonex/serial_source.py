"""The live frame source: the pedal's own dumps, read-only (issue #163).

Opens the CDC serial port for READING only and collects the 0x0204 preset
dump the pedal emits on every program change. Stepping the pedal through
its programs is done by the player (or by MIDI Program Change from the
probe tool, which is a select, not a write to storage); this source never
writes a byte to the port. Not exercised in tests: they use recorded
frames through RecordedFrames or the committed fixture.
"""
from __future__ import annotations

import os
import time

from . import frames as fr


class LiveFrames:
    """Callable source over a serial port path. Reads what the pedal sends
    during `window` seconds and splits it into frames; whatever programs it
    saw are returned, the rest stay empty (the adapter reports them as
    undecoded, never invents them)."""

    def __init__(self, port: str, window: float = 2.0):
        self.port = port
        self.window = window
        self.bytes_written = 0            # stays 0; there is no write path

    def read_raw(self) -> bytes:
        fd = os.open(self.port, os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY)
        try:
            got, end = b"", time.monotonic() + self.window
            while time.monotonic() < end:
                try:
                    chunk = os.read(fd, 4096)
                    if chunk:
                        got += chunk
                except BlockingIOError:
                    pass
                time.sleep(0.004)
            return got
        finally:
            os.close(fd)

    @staticmethod
    def split(stream: bytes) -> list[bytes]:
        """Frames are flag-delimited (0x7E ... 0x7E); adjacent frames share
        no flag on this pedal, so split on the pairs."""
        out, cur, inside = [], bytearray(), False
        for b in stream:
            if b == fr.FRAME_LEAD:
                if inside and len(cur) > 1:
                    cur.append(b)
                    out.append(bytes(cur))
                    cur, inside = bytearray(), False
                    continue
                cur, inside = bytearray([b]), True
                continue
            if inside:
                cur.append(b)
        return out

    def __call__(self) -> dict[int, bytes]:
        # A dump does not carry its own program number; without the MIDI
        # side reporting the PC alongside, the only honest mapping is by
        # arrival order from PC 0, which the probe tool drives. Here we
        # return frames keyed by arrival index and leave the rest empty.
        frames = self.split(self.read_raw())
        return {i: f for i, f in enumerate(frames) if i < 128}
