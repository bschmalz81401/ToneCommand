# Hardware Validation Report: FM9 editor-protocol feasibility

Date: 2026-08-16. Hardware: Fractal FM9, firmware 11.00 (build Jan 19 2026), USB MIDI on macOS (Apple Silicon). Test stack: Python 3.12, mido + python-rtmidi.

## Verdict

Reliable SysEx parameter control of the FM9 is achievable. Scene select, block bypass, and block channel are officially documented by Fractal. Individual block parameters (amp gain, presence, gate threshold, EQ bands) are not officially documented but the community has reverse-engineered the FM9-Edit editor protocol, with parameter get/set hardware-confirmed on a real FM9 at firmware 11.0. Proceed to Phase 2.

## Live hardware verification (all read-only queries, all successful)

- FM9 detected as class-compliant USB MIDI device, input and output port both named "FM9".
- Two-way SysEx confirmed with documented and community commands, model byte 0x12:
  - Query patch name (0x0D): preset 510 = "Xtra 4-VH4 Silver '25"
  - Query scene (0x0C): scene 1
  - Query scene name (0x0E): "RockRhythm"
  - Firmware query (0x08, unofficial but working): 11.00, build date string readable in payload
  - Status dump (0x13): returned all 16 blocks in the preset with bypass state and channel (amp id58, cab id62, delay id70 bypassed on ch B, reverb id66 ch B, inputs/outputs, etc.)
- The unit also emits unsolicited command 0x01 editor-protocol packets over USB, confirming the reverse-engineered editor protocol is active on this firmware.

## Documentation status

Official ("Axe-Fx III MIDI for Third-Party Devices" Rev 1.4 PDF; covers FM9 with model byte 0x12):
- Header: F0 00 01 74 12 <cmd> <payload...> <checksum> F7. Checksum = XOR of all bytes from F0 through payload, AND 0x7F. Verified against hardware and against all Gift of Tone files.
- Documented commands: block bypass get/set (0x0A), block channel get/set (0x0B), scene get/set (0x0C), preset name query (0x0D), scene name query (0x0E), looper (0x0F), tap tempo (0x10), tuner on/off (0x11), status dump (0x13), tempo BPM get/set (0x14). Effect IDs enumerated in the PDF appendix (Amp = DISTORT1 = 58, GATE1 = 146, etc.).
- Preset switching: standard Program Change + CC0 bank select (FM9 ignores CC32).
- Caveat: tuner/tempo push data streams only over the 5-pin MIDI jack, not USB.

Not official, but community-mapped (mcp-midi-control, ForgeFX/Axis, FracTool):
- Parameter get/set uses command 0x01 with 2-byte sub-actions; values are float32 packed into 5 septets; ~1,380 parameter IDs mined from FM9-Edit; round-trips hardware-confirmed on FM9 fw 11.0.
- Risk profile: unofficial, firmware-version-sensitive. Mitigation: keep all parameter IDs and sub-action bytes in a config file, pin to firmware 11.x, re-verify after any firmware update.

Key references:
- Official PDF: https://www.fractalaudio.com/downloads/misc/Axe-Fx%20III%20MIDI%20for%203rd%20Party%20Devices.pdf
- Fractal wiki: https://wiki.fractalaudio.com/wiki/index.php?title=MIDI_SysEx
- https://github.com/TheAndrewStaker/mcp-midi-control (deepest editor-protocol docs, FM9-confirmed)
- https://github.com/sKuhLight/ForgeFX and https://github.com/sKuhLight/Axis (open-source editor implementations)
- https://github.com/tysonlt/AxeFxControl (official spec implementation)

## Gift of Tone .syx analysis (local reference only)

- Envelope fully decoded: header msg (cmd 0x77, carries destination slot), 8 body msgs (0x78, 3082 bytes each; Axe-Fx III presets have 16), footer (0x79, whole-dump checksum). XOR checksum valid on every message in every file tested.
- Model bytes in files confirm device IDs empirically: III = 0x10, FM3 = 0x11, FM9 = 0x12.
- Body encoding decoded: 16-bit little-endian words packed 3 septets per word. Chunk 0 starts with 0xAA55 magic, a checksum word, then the preset name as plain ASCII padded to 32 chars (verified on Marco Fanton, Tosin, Neal Schon presets).
- The dense parameter region beyond the name is opaque binary (possibly further encoded/compressed); community has not resolved it. Conclusion: file-level parameter editing stays out of scope, as planned. Live MIDI is the control path.

## Phase 2 plan (approved scope)

1. Wrapper functions: scene select (0x0C, official), block bypass (0x0A, official), then parameter set via community 0x01 protocol for amp gain, presence, gate threshold.
2. All work targets the edit buffer only; the editor-protocol store command (sub 0x26) will not be implemented in the write path. Test on a scratch preset slot; explicit user-confirmed save only.
3. Parameter IDs, sub-action bytes, and firmware pin live in a config file for easy update after firmware changes.
