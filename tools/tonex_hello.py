#!/usr/bin/env python3
"""Send the documented ToneX hello handshake and print whatever comes back.

FIRST EVER WRITE to this pedal's serial port. Read tools/tonex_probe.py's
header for why that sentence deserves care: this channel is the one firmware
travels over, and until the librarian protocol is decoded (#27) nothing
speculative may be transmitted on it.

What this sends is NOT speculative. It is the hello handshake documented by
the community RE in vit3k/tonex_controller, which asks the pedal for its
firmware version. It writes nothing to storage. Its checksum is computed
with the same CRC-16/X-25 that validates all 128 of our reference captures,
so the framing is verified rather than assumed.

The one thing that is NOT verified: those bytes are documented for the ToneX
ONE, and this is aimed at a ToneX PEDAL. The community controller supports
both, which suggests the protocol is shared, but that is inference. If the
pedal ignores it, we learn that. If the pedal sulks, power cycle it.

Usage:  python3 tools/tonex_hello.py           # hello only
        python3 tools/tonex_hello.py --state   # then the state request
"""
from __future__ import annotations

import argparse
import glob
import os
import time
import tty

# Frames from vit3k/tonex_controller protocol.md, payload only. The flags,
# byte stuffing and FCS are applied below so the checksum is ours, computed
# by the routine that validates real captures, rather than copied.
HELLO_BODY = bytes.fromhex("b9030082040080 0b01b902020b".replace(" ", ""))
STATE_BODY = bytes.fromhex("b9030082060080 0b03b90281 06030b".replace(" ", ""))

FLAG, ESCAPE = 0x7E, 0x7D


def fcs(payload: bytes) -> int:
    """CRC-16/X-25, the HDLC frame check sequence. Validates 128/128 of our
    captures; the other four common CRC-CCITT variants validate none."""
    crc = 0xFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc ^ 0xFFFF


def stuff(body: bytes) -> bytes:
    out = bytearray()
    for b in body:
        if b in (FLAG, ESCAPE):
            out += bytes([ESCAPE, b ^ 0x20])
        else:
            out.append(b)
    return bytes(out)


def build(body: bytes) -> bytes:
    return bytes([FLAG]) + stuff(body + fcs(body).to_bytes(2, "little")) + bytes([FLAG])


def exchange(fd: int, frame: bytes, label: str, wait: float = 3.0) -> bytes:
    print(f"\n-> {label}: {frame.hex(' ')}  ({len(frame)} bytes)")
    os.write(fd, frame)
    got, end = b"", time.monotonic() + wait
    while time.monotonic() < end:
        try:
            chunk = os.read(fd, 4096)
            if chunk:
                got += chunk
                end = time.monotonic() + 0.4
        except BlockingIOError:
            pass
        time.sleep(0.004)
    if got:
        print(f"<- {len(got)} bytes")
        print(f"   {got.hex(' ')}")
        printable = "".join(chr(c) if 32 <= c < 127 else "." for c in got)
        print(f"   {printable}")
    else:
        print("<- silence")
    return got


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", action="store_true",
                    help="also send the state request after the hello")
    args = ap.parse_args()

    ports = sorted(glob.glob("/dev/cu.usbmodem*"))
    if not ports:
        raise SystemExit("no /dev/cu.usbmodem* found; is the pedal connected?")
    dev = ports[0]
    print(f"device: {dev}")
    print("NOTE: quit the TONEX Editor first, it holds this port exclusively.")

    fd = os.open(dev, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        tty.setraw(fd)          # raw: no cooked-mode mangling, no XON/XOFF
        end = time.monotonic() + 0.4
        while time.monotonic() < end:       # drain anything stale
            try:
                os.read(fd, 4096)
            except BlockingIOError:
                pass
            time.sleep(0.005)

        exchange(fd, build(HELLO_BODY), "HELLO (firmware version)")
        if args.state:
            exchange(fd, build(STATE_BODY), "STATE REQUEST")
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
