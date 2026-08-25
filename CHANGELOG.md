# Changelog

Notable changes to ToneCommand. Dates are UTC.

## Unreleased

### Changed (2026-08-24 session)
- The adapter contract declares capabilities instead of assuming them.
  fm9/adapter.py adds Capabilities and a ranked ReadPath (NONE <
  OBSERVED < DEVICE < EARS, making invariant 4's ranking comparable so a
  mixed rig reports its weakest link rather than an average). The
  contract previously assumed every method was answerable everywhere,
  which left an adapter on a device without a read path choosing between
  inventing state and failing; now it can say what it cannot do and the
  layer above degrades openly. Declaring is deny-by-default, so an
  unfinished adapter under-promises. A second real device is what
  surfaced this, including the shape the contract could not express: one
  device whose read and write paths are different transports.
- Invariant 0 is now architecture rather than one class's policy.
  fm9/safety.py holds the deny-by-default SendGuard every device
  transport passes through; a transport that declares no allowlist can
  send nothing. The never-brick check previously lived inside
  FM9._send, which protected the FM9 and left any second adapter with
  no protection at all. The FM9's own allowlist and behaviour are
  unchanged, and the refusal is still a PermissionError for callers
  that predate the lift.

### Fixed (2026-08-25 session)
- ToneX frame decoding was correct by luck rather than by
  understanding. It ignored HDLC byte stuffing entirely (0x7d escapes,
  next byte XOR 0x20; present in 36 of the 128 reference captures) and
  left the frame check sequence unverified. tools/tonex_decode.py now
  unstuffs and validates the FCS, which is CRC-16/X-25: established
  empirically rather than assumed, since of the five common CRC-CCITT
  variants it is the only one that validates, and it validates all 128
  captures. A validated CRC is the difference between a frame parsed
  correctly and one parsed without crashing. Decoded values are
  unchanged (the escapes fell in the FCS region), so earlier analysis
  stands. Frames without delimiters report the CRC as unchecked rather
  than as valid.

### Added (2026-08-24 session)
- tools/tonex_probe.py: read-only Phase 1 feasibility probe for the IK
  Multimedia ToneX pedal. Outbound traffic is limited to Program and
  Control Change by the shared SendGuard, and the pedal's serial
  control port is opened read-only, since firmware and bootloader
  traffic travels over that kind of channel on an undecoded device.
### Added (AI settings in the UI, 2026-08-24)
- `GET`/`POST /api/ai-settings`, following the existing `/api/gig` pair, and
  an AI SETTINGS panel in the UI: pick Claude Code CLI, Claude API, Grok CLI
  or an OpenAI-compatible endpoint, with CLIProxyAPI's default prefilled.
  Takes effect on the next prompt with no restart and no `.env` edit
  (issue #24).
- The choice persists in a gitignored `ai_settings.json`, with the
  environment as the fallback when the file is absent. Precedence, highest
  first: the file, the environment including `.env`, the built-in default.
- The API key never reaches the browser. `GET` returns a `hasKey` boolean
  and nothing more; a blank or absent key on `POST` keeps whatever is
  stored, and removing one takes an explicit `clearKey`.
- Only backends the host can actually run are selectable. The rest are
  shown disabled with the reason, because a dead option that silently falls
  through to something else is worse than no option.
- Only the controls a backend actually reads are shown, for the same reason.
  The four backends read different variables and two read none at all: the
  Claude CLI has nothing to configure and its model is a planner constant;
  the Claude API takes a key (`ANTHROPIC_API_KEY`) and its model is also a
  constant; the Grok CLI takes a model (`GROK_CLI_MODEL`) and no key; the
  OpenAI-compatible path takes all three. Auto carries the same three as
  the OpenAI path, since a configured endpoint is the planner's first
  candidate.
- Keys and models are stored per backend, so a router key cannot quietly
  become an Anthropic one, and a value cannot steer a backend that never
  reads it.
- A finished plan says which backend and model produced it, so a
  wrong-sounding plan is attributable to the model rather than the tool.
- `fm9/ai_settings.py` deliberately changes no planner behaviour: it writes
  the saved choice onto the same environment the planner already reads, so
  a UI selection and a hand-edited `.env` take exactly the same path.


### Added (planner backends, 2026-08-24)
- **OpenAI-compatible planner backend** (`PLANNER_BASE_URL`): reaches
  CLIProxyAPI, and through it Claude Code, Codex, Grok, Gemini or Kimi over
  their own OAuth logins, plus local models and OpenRouter. No new
  dependency - urllib, not the openai package. `PLANNER_API_KEY` is
  optional by design, since an OAuth router needs none.
- **Grok CLI planner backend** (`PLANNER_BACKEND=grok`), with replies
  constrained by `--json-schema` to `PLAN_SCHEMA`. Verified on grok 1.0.5.
  Reached only when pinned or through a router, never auto-selected.
- **Failure taxonomy and per-attempt record** in `plan()`, implementing
  @Triumph1701's contract from #7: transport or malformed output is a
  backend failure and moves on; a reply that parses but says nothing is a
  planner result and does not fall through; the aggregate error is raised
  only after every candidate is exhausted, naming each attempt.
- Every plan now carries `backend`, `model`, `plan_quality` and `attempts`,
  plus one log line, so backend choice is visible before the settings UI
  lands.
- `PLANNER_BACKEND` pins a backend and disables fallthrough; a deliberate
  choice must not quietly resolve to another vendor's model.
- README: planner backend table, and instructions for installing and
  running CLIProxyAPI yourself. It is a separate service, deliberately not
  vendored and not a dependency.

### Fixed (planner backends, 2026-08-24)
- `_env` distinguishes a variable that is ABSENT from one that is PRESENT and
  empty. Only an absent one falls through to `.env`; a blank means
  deliberately blank and stops the search, resolving to the built-in default.
  Treating them the same left no way for a layer above to say "not set", so
  the settings panel selecting Auto could not clear a `PLANNER_BACKEND` pin
  written into `.env` (@Triumph1701 on #25). A blank still resolves to the
  default, so an empty `CLAUDE_CLI_MODEL` means the built-in model rather
  than `--model ""`.
- The Claude API backend is bounded by `PLANNER_TIMEOUT` like every other
  backend, with timeouts and connection errors mapped to `timeout` and
  `transport` failures. It was the one backend not honouring the contract
  this work introduced: the SDK default plus its retries applied, so a stuck
  call hung `/api/plan` with no failure and no fall-through.
- `GROK_ENV_KEYS` includes `NETWORK_ENV_KEYS`. Withholding the proxy and CA
  variables from the grok CLI reproduced exactly the failure that set exists
  to prevent, and the test asserted the broken behaviour. Narrowing per tool
  means narrowing which credentials it sees, not starving it of the shell:
  no Anthropic or cloud keys reach it, which the test now checks explicitly.
- The test isolation fixture clears `CLAUDE_CLI_MODEL` and
  `CLAUDE_API_MODEL`. Both are new here and were left out, so a developer
  with either exported got a false failure from the test asserting the
  built-in default.
- Planner subprocesses get an environment allowlist instead of
  `os.environ`. The `claude` binary had been receiving every secret in the
  process; with a second vendor's CLI in play an xAI binary would have
  received `ANTHROPIC_API_KEY`.
- The Claude CLI path gained the timeout and empty-output cases it was
  missing, and reports the model from `modelUsage` rather than a top-level
  `model` key, which a real envelope does not carry - reading `model` alone
  reported the alias we asked for.
- Planner subprocesses get an environment allowlist wide enough to keep
  working setups working: proxy and CA variables, the CLI's config dir, and
  the Bedrock and Vertex routes are configuration rather than foreign
  secrets. Each CLI still sees only its own credentials.
- `PLANNER_TIMEOUT` is parsed safely and per call. It was an unguarded
  `int()` at import, so a dotenv-style `PLANNER_TIMEOUT=300  # comment`
  crashed `import fm9.planner` and took the server down at startup, for
  users who never plan anything.
- The OpenAI-compatible path enforces a real wall-clock deadline. urllib's
  timeout bounds each socket operation, not the attempt, so a router that
  trickles its body never tripped it and `/api/plan` hung with no timeout
  failure and no fall-through.
- The two Claude models are configurable instead of hard-coded:
  `CLAUDE_CLI_MODEL` and `CLAUDE_API_MODEL`, defaulting to the previous
  constants (`sonnet` and `claude-opus-5`). The CLI has always taken
  `--model` and the SDK a model id, so neither needed to be fixed, and
  wanting Opus on the CLI path is a reasonable thing to want. Read per call,
  so a change does not wait for a restart, and passed through the subprocess
  allowlist.
- JSON extraction tries each `{` with the stdlib decoder and prefers the
  last plan-shaped object. Slicing from the first brace to the last one
  broke on real local-model output: a reasoning model drafts an object and
  then emits its final answer, and that span covers both, failing with
  "Extra data: line 2 column 1". Found by pointing the OpenAI-compatible
  backend at LM Studio, which is what issue #7 asked for. Counting braces
  in one pass is not enough either, as @Triumph1701 pointed out on #21: a
  model that abandons a draft part way leaves an unclosed brace and an
  unterminated quote behind, which pin the depth and swallow the rest of
  the reply, losing the real answer that follows. Trying each start in turn
  costs a bad start only that start.
- `.env` values are unquoted. `PLANNER_API_KEY="sk-local"` was sending
  `Bearer "sk-local"`, and a quoted base URL failed as an unknown url type.
- Plan validation runs inside the per-backend try, so a reply that parses
  as JSON but is shaped wrongly (`{"actions": 42}`) falls through to the
  next backend instead of aborting the run untyped.
- An explicit JSON `null` for a non-nullable action field no longer costs
  the whole plan a 502; nulls are replaced, not merely defaulted when absent.
- `_api_available()` checks for the key instead of the mere existence of a
  `.env` file, so a router-only install stops offering a doomed `api`
  candidate whose auth noise buried the actionable transport failure.

### Fixed (docs, 2026-08-24)
- docs/HARDWARE-VALIDATION.md is marked as a preserved 2026-08-16 snapshot
  rather than current documentation, listing what has been superseded since
  - the firmware 11.x pin, and its statement that the store command would
  never be implemented in the write path (it is, whitelisted). The body is
  left as written; a dated report is worth more as a record than as a
  document quietly edited to stay true.
- The README's claim that FM9-Edit resets the edit buffer when it connects
  was wrong. Tested with FM9-Edit 1.03.21 on fw 12.00: unsaved edits
  survived the editor connecting, and reads stayed correct while it polled
  the shared port at ~60 msg/s. Buffer edits are lost to a preset load from
  either side, which is ordinary behaviour. Concurrent writes, older editor
  versions and fw 11.00 remain untested and are marked as such.
  docs/PROTOCOL.md finding 23.
- README compatibility table and Protocol Contributions brought current
  with what fw 12.00 has actually proven.

### Fixed (preset numbering, 2026-08-24)
- Tools now print preset numbers both ways: the wire number (0-511) and the
  number FM9-Edit and the front panel show for the same slot (1-512). They
  differ by one, and a bare wire number is how the wrong preset gets
  cleared. Found by the owner cross-checking a built chain against
  FM9-Edit.
- Out-of-range preset numbers are refused instead of believed. The unit
  answers a query for preset 512 with a blank name, and a blank is not the
  `<EMPTY>` marker, so an unguarded read called such a slot OCCUPIED - the
  wrong direction for code choosing where to write.
- `TONECOMMAND_STORE_SLOTS` is documented as wire-numbered: `133-148` is
  what the editor shows as 134-149.
- The two surfaces where being wrong actually costs something now print both
  numbers too, which the first pass missed (@Triumph1701 on #22). The store
  confirmation is the only destructive prompt in the product, and it named a
  slot the owner's own editor disagreed with, so reading the dialog and
  checking FM9-Edit was how a correct operation got aborted or a wrong one
  approved. The live preset readout had the same fault with less at stake.
  Both labels are rendered server side from `protocol.slot_label`, so the
  numbering rule stays in one place instead of being recomputed in the
  browser.
- A store refusal describes the whitelist it is enforcing rather than its
  endpoints. With `TONECOMMAND_STORE_SLOTS=133,150-155`, refusing slot 140
  used to print "configured store slots are 133-155", naming the refused
  slot as allowed and sending the owner off to fix the wrong thing. Runs are
  collapsed, so a contiguous whitelist still reads as one range.
- docs/PROTOCOL.md findings 21-22.

### Fixed (from-scratch tool, 2026-08-24)
- A device NACK during slot selection prints a refusal instead of a
  traceback. `NoEmptySlot` and `FM9NotFound` are both `RuntimeError`, but
  `_request` raises the bare parent, and naming only the children let it
  escape the handler.
- An inverted `--range 449 386` is refused rather than scanning nothing and
  announcing that every slot holds a preset, which told the owner their unit
  was full when it may have been empty. Checked in `scan_slots`, so every
  caller is covered rather than just the tool.
- docs/PROTOCOL.md finding 6 lists row 3 among the verified same-row cable
  runs. Finding 20 added it and the simulator already relies on it, so the
  ledger entry the cable code cites was out of step with the code.
- The fw 12.00 compatibility row for block insert reads plain "Verified":
  this work verified it firsthand on the owner's unit, not via a
  contributor report.

### Added (from-scratch builds, 2026-08-24)
- `tools/build_from_scratch.py`: builds INPUT -> amp -> cab -> OUTPUT into
  an empty preset slot, placing every block and drawing every cable, then
  verifying the chain is continuous. Edit buffer only; nothing is stored.
- `FM9.first_empty_slot()`: finds a free slot, or raises `NoEmptySlot`. The
  build always lands on a slot the device itself reports as `<EMPTY>` and
  refuses when there is none - there is no `--force`, because overwriting a
  preset someone owns should not be one flag away.
- docs/PROTOCOL.md findings 18-20: an empty slot has no grid cells and no
  Input/Output blocks (only the ever-present ids 200/201); placing into a
  blank grid works, arriving uncabled; row-3 same-row cable draws work with
  the general formula, owner-confirmed audible.


### Added (2026-08-23 session)
- tools/apply_template.py: apply any owner-defined 8-scene layout to a
  preset from a mapping file; mechanics only, conventions stay local.
- tools/path_audit.py: end-to-end signal-path proof per scene (grid
  walk, alias-aware, send/return bus, source-block bypass semantics).
- tools/preset_doctor.py: the full verification ladder as one command.
- tools/conventions.py + optional local kb/conventions.json: owner
  conventions (trims, staircase, name vocabularies) enforce only when
  configured; public tools ship without opinions.
- DeviceAdapter contract: slot_name / is_slot_empty (by-number reads).
- Level report: staircase and boost-below-reference checks
  (convention-gated); scene audit: bypassed-INPUT and severed-Return
  flags, dual-instance sweeps.

### Fixed (2026-08-23 session)
- Seven presets carried silent scenes (bypassed Input blocks); the
  class is now flagged by the audit and proven dead-or-alive by the
  path audit.
- Modifier bindings: full revive sequence that survives the device's
  load-time slot validation (docs/PROTOCOL.md findings 16-17); pedal
  delay/multitap bindings restored across the owner's presets.

### Added (empty-slot probe, 2026-08-23)
- `tools/find_empty_slots.py`: reports which preset slots are free, as
  contiguous ranges, and suggests a target for a from-scratch build.
  Non-destructive - it selects nothing, so it is safe to run mid-session
  with a preset you are playing loaded.
- `FM9.slot_name()` / `is_slot_empty()` / `scan_slots()`: read a slot's
  stored name by number, out of flash, without selecting it. fn 0x0D
  supports this and nothing here used it before; every other preset
  inspection in the project discards the edit buffer to do its work.
- `FM9.require_empty_slot()`: gate for building a preset from scratch, so
  a build cannot start by clobbering a preset someone owns. Opt-in target
  check; store stays separately whitelisted.
- `protocol.SlotName`, `decode_name_field()`, `is_empty_slot_name()`, and
  `EMPTY_SLOT_NAME`: the `<EMPTY>` marker is now a first-class concept
  instead of a string no code recognized.
- Simulator models empty slots (`SIM_EMPTY_SLOTS`), including the ghost
  bytes and the all-NUL scene-name fields, so all of the above is
  testable headless.

### Fixed (empty-slot probe, 2026-08-23)
- Preset names are cut at the first NUL instead of right-stripped.
  Clearing a slot overwrites only the first 8 bytes of the 32-byte name
  field, so `current_preset()` had been reporting names like
  `'<EMPTY>\x00 Phat Time'` - the marker glued to the tail of a preset
  that no longer exists. Replaying the new parser over 512 real captured
  name fields changes no occupied name and drops the ghost from all 72
  empty ones. See docs/PROTOCOL.md findings 14 and 15.

## 0.1.0 (2026-08-22)

First tagged release: installation is now repeatable, so the version
number means something.

### Added (release polish, 2026-08-22)
- Packaging: pyproject.toml with declared dependencies and a
  one-command launcher (`pip install -e .` then `tonecommand`).
- README: UI screenshot (captured against the bundled simulator),
  architecture diagram, "What you can say" examples, and an explicit
  capability/firmware compatibility table.
- docs/HARDWARE-VALIDATION.md: the hardware feasibility report,
  relocated from PHASE1-REPORT.md and retitled as public documentation.

### Changed (release polish, 2026-08-22)
- Tagline reworded from "Speak" to "Describe the tone you want":
  the shipped workflow is typed, and the pitch should not promise a
  voice input that does not exist yet.
- test_phase2.py renamed to hardware_regression.py; the two-tier test
  story (simulator suite in CI on every push, 13-check regression on
  hardware) is now documented in the README.
- CI installs from pyproject instead of an ad-hoc pip line, which also
  fixes a dependency typo (httpx2).

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
