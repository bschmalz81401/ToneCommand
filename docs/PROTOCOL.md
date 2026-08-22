# FM9 Editor Protocol: Findings Ledger

Wire-level findings from hardware verification on a Fractal FM9 (firmware
11.00 unless noted), offered as a reference for tool builders. Everything
here was proven by write-plus-readback on a real unit, by front-panel
cross-verification with a human reading the screen, or is explicitly
marked UNDECODED. Prior art: the mcp-midi-control project decoded the
foundations (see THIRD_PARTY_NOTICES); this ledger extends and corrects
it. Corrections to our own earlier claims are kept, marked SUPERSEDED.

## Frame basics

- Envelope: F0 00 01 74 <model> <fn> <payload...> <checksum> F7; model
  0x12 = FM9; checksum = XOR of all preceding bytes & 0x7F.
- 14-bit values: little-endian septet pairs (lo7, hi7).
- Float32: 5 septets, little-endian, 7 bits per septet.
- Strings (names): 8-to-7 packing, length-prefixed, chunked.
- SIGNED values (e.g. pitch shift semitones): 16-bit two's complement on
  the wire; -12 = 65524. Positive values are plain. Verified by
  front-panel cross-check (owner turned the knob to -12, wire read
  65524).

## Function surface used

| fn | purpose | notes |
|---|---|---|
| 0x01 | editor sub-actions | see below |
| 0x0A | bypass get/set | set = payload with state byte; get = shorter |
| 0x0B | channel get/set | |
| 0x0C | scene get/set | 0x7F = get |
| 0x0D/0x0E | preset / scene name | |
| 0x13 | status dump | blocks + bypass + channel; the most honest state read |
| 0x14 | tempo | 14-bit BPM |
| 0x1F | bulk param read | replies 0x74/0x75/0x76 burst; channel-blocked (index = channel * stride + paramId) |

fn 0x01 sub-actions: 0x09 discrete set (float32 ordinal), 0x52 continuous
set (normalized float32), 0x1F display-name query, 0x26 store, 0x28/0x2B
preset/scene rename, 0x2E grid read, 0x30 cell select (RAW uint32, not
float32), 0x32 grid insert, 0x35 cable draw.

## Verified behaviors

1. **Grid insert requires a preceding cell select** (sub 0x30, raw
   uint32). Without it the insert lands on the device's internal cursor.
   Status-dump verification cannot catch this; grid-read verification can.
2. **Grid-read effect IDs alias mod 128** (id stored as (id << 1) in 8
   bits). FX Return (186) reads as 58 and will masquerade as an amp in
   naive scans. Disambiguate with the fn 0x13 status dump.
3. **Writes are asynchronous.** A read immediately after a write returns
   the PRE-write state with no error. Settle ~150ms and verify by
   read-back. Every "verified" claim in this ledger means exactly that.
4. **A zero-valued discrete set is a GET, not a set.** Sub 0x09 with
   value 0 is the device's zeroed-GET no-op, so ORDINAL 0 CANNOT BE SET
   through the discrete path: the write silently does nothing. Route
   ordinal 0 through a continuous 0.0 write instead. (Found by replay
   testing; an earlier session's "Small Room" (ordinal 0) reverb change
   is believed to have silently no-opped this way.)
5. **The display-name query (sub 0x1F) is a trap.** For the amp block it
   returns the roster's FIRST entry regardless of the actual amp type,
   before and after writes, through seconds of settle (fw 11.00 and
   12.00, verified independently by two contributors). For modifier
   source enums it returns "NONE"; for other type enums a stale
   constant. Never verify any type through it: read the wire value and
   map through a roster. SUPERSEDED: our earlier "fresh only for the amp
   block" claim was a misread.
6. **Cable drawing (sub 0x35).** The community 6-row encoding formula
   draws correct cables for: adjacent-row diagonals (verified
   repeatedly), row-4 and row-5 same-row runs. Row-2 same-row runs use
   their OWN encoding (odd source column: dest_sign 0 / b23 3; even:
   dest_sign 1 / b23 1), decoded by probe. UNDECODED: draws from row 1
   of even columns; 2-row-plus diagonals (they do not register at all).
   Cable draw is IDEMPOTENT: re-sending does not remove a cable; the
   removal message is UNKNOWN.
7. **Shunt-replacement inheritance is IN-side only.** Placing a block on
   a shunt keeps the shunt's incoming cable but can DROP the outgoing
   one, silently severing everything downstream. After any insert, read
   the next cell's input mask and redraw if zero.
8. **Shunts cannot be inserted** (no effect id lands one via sub 0x32).
   A unity Volume block is the practical pass-through hop.
9. **Inserts are silently refused over the DSP budget.** Nothing lands,
   no error. Diagnose by retrying the same insert on a light preset.
10. **Param existence is type-dependent.** Writes to params the current
    type does not expose clamp or misbehave (pitch shift under Dual
    Detune clamps at 24). Set the type FIRST, then its params.
11. **Front-panel enum lists are not enum order.** A dictated type list
    placed Dual Chromatic 4th; the wire says ordinal 2. Verify each
    ordinal by wire. Wire-verified pitch types so far: Dual Detune = 0,
    Dual Chromatic = 2 (the FM9's POG equivalent via +12/-12 voices).
12. **Modifier bindings from scratch are UNDECODED** (reversed or dead
    sweeps). Working practice: clone a proven slot within the same
    preset context and retarget only its target effect/param ids,
    leaving the curve bytes untouched. Live modulation (a moving pedal)
    is invisible to every known read: verify sweeps by ear.
13. **Read honesty ranking** for anything audible: human ear > fn 0x13
    status dump > bulk read (0x1F) > get-style reads > display-name
    query (never). Channel-indexed reads require the channel count cache
    to be populated (a status dump) or every channel silently reads as
    channel A.

## Undecoded territory (help welcome)

Cable removal; multi-row diagonal draw encoding; row-1 even-column
source draws; modifier curve semantics for from-scratch bindings; most
type-enum ordinal tables (delay, chorus, reverb beyond name lists);
DSP budget introspection; the precise conditions under which the
per-scene bypass GET (fn 0x0A) disagrees with the status dump (observed
once contradicting audible reality; status dump has matched the ears
every time since - use it).
