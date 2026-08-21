# Changelog

Notable changes to ToneCommand. Dates are UTC.

## Unreleased

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

### Protocol findings (README "Protocol Contributions")
- Row-2 same-row cable encoding; cable draw is idempotent (removal is a
  different, unknown message); 2-row diagonal draws do not register.
- Writes are asynchronous; unsettled reads return plausible stale values.
- Amp display-name query behavior differs by firmware (under investigation
  with @bschmalz81401, #15).
