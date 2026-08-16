#!/usr/bin/env python3
"""Structural analysis of Fractal .syx preset files (local research only).

Reads .syx files, splits them into SysEx messages (F0 ... F7), and reports:
- message count, sizes
- header bytes (manufacturer ID 00 01 74, model ID, command byte)
- checksum check (XOR of all bytes F0..payload, & 0x7F, as last byte before F7)
- ASCII strings found (preset name etc.)
"""
import sys
from pathlib import Path
from collections import Counter


def split_sysex(data: bytes):
    msgs = []
    i = 0
    while i < len(data):
        if data[i] == 0xF0:
            j = data.find(b"\xf7", i)
            if j == -1:
                msgs.append(("UNTERMINATED", data[i:]))
                break
            msgs.append(("SYSEX", data[i : j + 1]))
            i = j + 1
        else:
            j = data.find(b"\xf0", i)
            if j == -1:
                msgs.append(("JUNK", data[i:]))
                break
            msgs.append(("JUNK", data[i:j]))
            i = j
    return msgs


def xor_checksum_ok(msg: bytes) -> bool:
    # msg = F0 ... checksum F7
    if len(msg) < 4:
        return False
    calc = 0
    for b in msg[:-2]:
        calc ^= b
    return (calc & 0x7F) == msg[-2]


def ascii_runs(payload: bytes, minlen=4):
    runs, cur = [], []
    for b in payload:
        if 0x20 <= b <= 0x7E:
            cur.append(chr(b))
        else:
            if len(cur) >= minlen:
                runs.append("".join(cur))
            cur = []
    if len(cur) >= minlen:
        runs.append("".join(cur))
    return runs


def analyze(path: Path, verbose_first=3):
    data = path.read_bytes()
    msgs = split_sysex(data)
    sysex = [m for kind, m in msgs if kind == "SYSEX"]
    junk = [m for kind, m in msgs if kind != "SYSEX"]
    print(f"\n=== {path.name} ({len(data)} bytes) ===")
    print(f"sysex messages: {len(sysex)}, non-sysex bytes: {sum(len(j) for j in junk)}")
    if not sysex:
        return
    sizes = Counter(len(m) for m in sysex)
    print(f"message sizes: {dict(sorted(sizes.items()))}")
    headers = Counter(m[1:6].hex(" ") for m in sysex)
    print(f"headers (mfr+model+cmd): {dict(headers)}")
    cks = sum(1 for m in sysex if xor_checksum_ok(m))
    print(f"xor-checksum valid: {cks}/{len(sysex)}")
    names = []
    for m in sysex[:verbose_first] + sysex[-1:]:
        names += ascii_runs(m, minlen=5)
    if names:
        print(f"ascii strings (first {verbose_first} + last msgs): {names[:10]}")
    # first message hexdump (first 48 bytes)
    print(f"msg[0][:48]: {sysex[0][:48].hex(' ')}")
    if len(sysex) > 1:
        print(f"msg[1][:32]: {sysex[1][:32].hex(' ')}")
        print(f"msg[-1][:32]: {sysex[-1][:32].hex(' ')}")


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        analyze(Path(arg))
