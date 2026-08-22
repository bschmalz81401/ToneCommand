# Changelog

Notable changes to ToneCommand. Dates are UTC.

## Unreleased

### Added (2026-08-22)
- Tone recipes: shareable, cited, replayable builds (docs/RECIPES.md,
  tools/replay_recipe.py, first recipe published). Store is forbidden by
  format; every replay ends in an ear checklist.
- docs/PROTOCOL.md: the hardware findings ledger as a citable spec,
  including the zero-ordinal GET trap, the display-name trap, cable
  encoding status, and the read-honesty ranking.
- Tone lock (tools/tone_lock.py): wire-level regression testing for
  presets; lock a baseline, detect any drifted parameter by name.
- Gig mode: POST /api/gig locks the server to scene changes only (HTTP
  423 for everything else) for the duration of a performance.
- DSP budget advisor (tools/budget_advisor.py): predicts silent insert
  refusals from the owner's own preset library instead of a fake CPU
  model - it correctly "predicts" the stereo-pair refusal of 2026-08-21.

### Fixed
- Ordinal 0 could never be set through the discrete path (zero-valued
  sub 09 is the device's GET); zero ordinals now route through a
  continuous 0.0 write. Earlier zero-ordinal type sets may have silently
  no-opped; hardware re-verification queued.

### Added
- Complete grounding data: amps 331/331, drives 86/86, cab IRs 2,235/2,237
  plus all 45 DynaCabs (cabs via @bschmalz81401, #14), and 34 delay/chorus/
  multitap type references, all facts-only with citations.
- Simulator fidelity: async-write settle window (unsettled reads return
  pre-write state, like hardware) and undecoded-territory tracking (the sim
  names what no hardware session has verified instead of silently
  simulating it).
- Read-only tooling: preset inspector (tone report of any preset) and tone
  library harvester (voicing references from curated on-device presets;
  output stays local, never committed).
- Device snapshot resolves the active cab IR to the real cabinet it models.
- Honesty warnings: add_block warns that factory defaults are not a
  finished sound; bind_pedal warns its curve direction is unverified (#11).

### Fixed
- Same-row cable draws on grid row 2 (hardware-decoded encoding; the
  general formula silently drew nothing).
- Channel cache auto-population (empty cache silently collapsed every
  channel read to channel A).
- FM9 port handling: loud preflight on poisoned ports, context-manager and
  atexit cleanup, close() deadline (zombie processes held the MIDI port and
  corrupted later sessions).
- A failed add_block aborts the remaining plan instead of binding pedals to
  blocks that never landed.

### Added (2026-08-21 session)
- Tone library harvested: all 512 on-device presets captured as voicing
  references (local-only), plus a per-scene consistency audit that caught
  and fixed a systemic dry-scene staging bug across the setlist.
- Effect-type grounding: 34 delay/chorus/multitap names mapped from wiki
  sources; pitch type ordinals begun (wire-verified, human-in-the-loop).
- add_block verifies and self-repairs the downstream cable after
  shunt-replacement.

### Protocol findings (README "Protocol Contributions")
- Negative signed params are 16-bit two's complement on the wire
  (-12 = 65524). Pitch types: Dual Detune = 0, Dual Chromatic = 2.
- Shunt-replacement inherits the incoming cable only; the outgoing side
  can silently drop. Row-4 same-row cable draws follow the general
  formula. Shunts cannot be inserted; a unity Volume block is the
  pass-through workaround. Inserts are silently refused over the DSP
  budget.
- Row-2 same-row cable encoding; cable draw is idempotent (removal is a
  different, unknown message); 2-row diagonal draws do not register.
- Writes are asynchronous; unsettled reads return plausible stale values.
- Amp display-name query behavior differs by firmware (under investigation
  with @bschmalz81401, #15).
