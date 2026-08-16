#!/usr/bin/env python3
"""Phase 1 handshake probe for Fractal FM9 over USB MIDI.

READ-ONLY: sends only documented query commands (query patch name, query
scene, status dump, firmware query). Never sets or stores anything.
"""
import sys
import time

import mido

MFR = [0x00, 0x01, 0x74]
MODEL_FM9 = 0x12

CMD_FIRMWARE = 0x08      # unofficial but community-confirmed
CMD_SCENE = 0x0C         # 0x7F = query
CMD_PATCH_NAME = 0x0D    # 0x7F 0x7F = current preset
CMD_SCENE_NAME = 0x0E    # 0x7F = current scene
CMD_STATUS_DUMP = 0x13


def checksum(body):
    # XOR of F0 + all body bytes, masked to 7 bits
    x = 0xF0
    for b in body:
        x ^= b
    return x & 0x7F


def build(cmd, payload=()):
    body = MFR + [MODEL_FM9, cmd] + list(payload)
    return mido.Message("sysex", data=body + [checksum(body)])


def find_fm9_ports():
    ins = [n for n in mido.get_input_names() if "fm9" in n.lower()]
    outs = [n for n in mido.get_output_names() if "fm9" in n.lower()]
    return ins, outs


def parse_response(data):
    d = list(data)
    if d[:3] != MFR or len(d) < 5:
        return f"non-Fractal sysex: {bytes(d[:12]).hex(' ')}..."
    model, cmd = d[3], d[4]
    payload = d[5:-1]  # strip trailing checksum
    if cmd == CMD_PATCH_NAME and len(payload) >= 34:
        num = payload[0] | (payload[1] << 7)
        name = "".join(chr(c) for c in payload[2:34]).rstrip()
        return f"PATCH NAME: preset {num} = \"{name}\""
    if cmd == CMD_SCENE_NAME and len(payload) >= 33:
        name = "".join(chr(c) for c in payload[1:33]).rstrip()
        return f"SCENE NAME: scene {payload[0] + 1} = \"{name}\""
    if cmd == CMD_SCENE:
        return f"SCENE: current scene = {payload[0] + 1}"
    if cmd == CMD_FIRMWARE:
        return f"FIRMWARE: {payload[0]}.{payload[1]:02d} (raw {bytes(payload).hex(' ')})"
    if cmd == CMD_STATUS_DUMP:
        blocks = []
        for i in range(0, len(payload) - 2, 3):
            eid = payload[i] | (payload[i + 1] << 7)
            dd = payload[i + 2]
            bypassed = dd & 1
            chan = (dd >> 1) & 0x07
            blocks.append(f"id{eid}{'(byp)' if bypassed else ''}ch{'ABCD'[chan] if chan < 4 else chan}")
        return f"STATUS DUMP: {len(blocks)} blocks in preset: {', '.join(blocks)}"
    return f"cmd 0x{cmd:02X} (model 0x{model:02X}): {bytes(payload).hex(' ')}"


def main():
    ins, outs = find_fm9_ports()
    print("FM9 input ports:", ins)
    print("FM9 output ports:", outs)
    if not ins or not outs:
        print("\nAll MIDI inputs:", mido.get_input_names())
        print("All MIDI outputs:", mido.get_output_names())
        sys.exit("FM9 not found. Is it connected and powered on?")

    queries = [
        ("query current preset name", build(CMD_PATCH_NAME, [0x7F, 0x7F])),
        ("query current scene", build(CMD_SCENE, [0x7F])),
        ("query current scene name", build(CMD_SCENE_NAME, [0x7F])),
        ("query firmware version", build(CMD_FIRMWARE)),
        ("status dump (all blocks)", build(CMD_STATUS_DUMP)),
    ]

    with mido.open_input(ins[0]) as inp, mido.open_output(outs[0]) as outp:
        for label, msg in queries:
            print(f"\n>> {label}: F0 {bytes(msg.data).hex(' ')} F7")
            outp.send(msg)
            deadline = time.time() + 2.0
            got = False
            while time.time() < deadline:
                for resp in inp.iter_pending():
                    if resp.type == "sysex":
                        print("<<", parse_response(resp.data))
                        got = True
                if got:
                    break
                time.sleep(0.02)
            if not got:
                print("<< (no response within 2s)")


if __name__ == "__main__":
    main()
