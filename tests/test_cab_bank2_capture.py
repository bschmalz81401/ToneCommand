"""Issue #43: the user-cab write encoding is derived from a capture, never
assumed.

On 2026-09-19 the Pilot spied FM9-Edit's output to the FM9 (firmware 12.x)
with snoize MIDI Monitor while the editor wrote one cab file to U1.0523,
which is what the Lukather bundle map calls Bank 2 slot 11 (the editor
lists a single flat bank, U1.0001 to U1.1024). The write frames and the
unit's acks are in tests/fixtures/fm9edit-bank2-slot11.json with the body
chunks reduced to sha256 (they are a commercial IR); the raw capture and
the source file live outside the repository and, when present, the frames
`cabfile.retarget` builds are compared with the capture byte for byte.

What the head says, in the two candidate forms the code used to guess:
neither. Candidate A (tag carries the bank) would have sent `00 0A 00 11`;
candidate B as retarget laid it out would have sent `04 0A 00 10`. The
editor sent `0A 14 00 10`: candidate B's flat index 522 under tag 0x10,
with the index bytes low septet first and 0x10 OR'd into the high one.
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from fm9 import cabfile
from fm9 import protocol as p

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "fm9edit-bank2-slot11.json"
RAW_CAPTURE = ROOT / "kb" / "captures" / "fm9edit-bank2-slot11.syx"
SOURCE_FILE = Path.home() / "Documents/IR-Library/fractal-syx" \
    / "BT_Steve_Lukather__Fractal_-_07-2025/BT_3VH4Luke_1__bank2_num12.syx"


def _frame(hex_text: str) -> list[int]:
    return [int(x, 16) for x in hex_text.split()]


@pytest.fixture(scope="module")
def fx():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_capture_is_one_complete_dump_family(fx):
    head, tail = _frame(fx["head"]), _frame(fx["tail"])
    assert head[1:4] == list(p.MFR) and head[4] == p.MODEL_FM9
    assert head[5] == cabfile.FN_HEAD and tail[5] == cabfile.FN_FOOT
    assert p.checksum(head[1:-2]) == head[-2]
    assert p.checksum(tail[1:-2]) == tail[-2]
    assert len(fx["chunks"]) == 8
    assert all(c["len"] == 1290 for c in fx["chunks"])
    assert all(c["prefix"].startswith("F0 00 01 74 12 7B 00 02")
               for c in fx["chunks"])


def test_the_head_is_the_third_form_and_the_code_derives_it(fx):
    head = _frame(fx["head"])
    payload = head[6:-2]
    assert payload == [0x0A, 0x14, 0x00, 0x10]
    # not candidate A (tag 0x11 for bank 2), not retarget's old big-endian
    # index (`04 0A`): the flat index, low septet first, flag in the high
    assert payload[3] == cabfile.DEFAULT_TAG
    assert payload[1] & cabfile.HEAD_FLAG
    assert cabfile.head_index(payload) == fx["flat_index"] == 522
    assert cabfile.head_index_bytes(522) == payload[:2]
    # and the formula round-trips over the whole flat list
    for slot in (0, 1, 127, 128, 511, 512, 522, 1023):
        assert cabfile.head_index(cabfile.head_index_bytes(slot)) == slot


def test_the_source_files_own_heads_carry_the_same_layout(fx):
    # the library's files, exported by Fractal's own editor, encode their
    # slot the same way: 0C 10 is flat 12, num165's 25 11 is 37 + 128
    assert cabfile.head_index(_frame(fx["source_file_head"])[6:-2]) == 12
    assert cabfile.head_index([0x25, 0x11]) == 165


def test_the_unit_acked_every_frame_in_order(fx):
    seq = fx["sequence"]
    writes = [m for m in seq if m["dir"] == "To FM9"
              and int(m["fn"], 16) in cabfile.CAB_DUMP_FNS]
    assert [int(m["fn"], 16) for m in writes] == \
        [0x7A] + [0x7B] * 8 + [0x7C]
    for i, m in enumerate(seq):
        if m["dir"] == "To FM9" and int(m["fn"], 16) in cabfile.CAB_DUMP_FNS:
            ack = seq[i + 1]
            assert ack["dir"] == "From FM9" and ack["fn"] == "64"
            got = p.parse_multipurpose(_frame(ack["hex"])[1:-1])
            assert got == (int(m["fn"], 16), 0), (m, ack)
            assert ack["t_ms"] - m["t_ms"] < 150   # the tail took ~120 ms


def test_the_editor_then_read_the_slot_without_fn_0x19(fx):
    """After the write FM9-Edit asked fn 0x01 sub 0x4B with the same slot
    bytes and the unit answered at once. Recorded, not decoded, not used:
    a read-back candidate for a later issue. No fn 0x19 anywhere."""
    seq = fx["sequence"]
    fns = [int(m["fn"], 16) for m in seq]
    assert 0x19 not in fns
    q = [m for m in seq if m["dir"] == "To FM9" and m["fn"] == "01"]
    assert len(q) == 1
    assert _frame(q[0]["hex"])[6:8] == [0x4B, 0x00]
    assert _frame(q[0]["hex"])[12:14] == [0x0A, 0x14]


@pytest.mark.skipif(not (RAW_CAPTURE.exists() and SOURCE_FILE.exists()),
                    reason="local raw capture and source cab file not present")
def test_retarget_reproduces_the_captured_write_byte_for_byte(fx):
    src = SOURCE_FILE.read_bytes()
    assert hashlib.sha256(src).hexdigest() == fx["source_file_sha256"]
    captured = cabfile.parse(RAW_CAPTURE.read_bytes(), "capture.syx").frames
    built = cabfile.retarget(cabfile.parse(src, SOURCE_FILE.name), 522)
    assert built == captured
    assert [hashlib.sha256(bytes(f)).hexdigest() for f in captured[1:-1]] \
        == [c["sha256"] for c in fx["chunks"]]


@pytest.mark.skipif(not SOURCE_FILE.exists(),
                    reason="local source cab file not present")
def test_retarget_masks_every_fifth_body_byte_as_the_editor_did(fx):
    cf = cabfile.parse(SOURCE_FILE.read_bytes(), SOURCE_FILE.name)
    built = cabfile.retarget(cf, 522)
    assert [hashlib.sha256(bytes(f)).hexdigest() for f in built[1:-1]] \
        == [c["sha256"] for c in fx["chunks"]]
    for f in built[1:-1]:
        for j in range(cabfile.CHUNK_DATA_OFFSET + cabfile.CHUNK_GROUP - 1,
                       len(f) - 2, cabfile.CHUNK_GROUP):
            assert f[j] & 0x70 == 0
