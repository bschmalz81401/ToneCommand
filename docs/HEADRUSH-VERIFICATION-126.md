# HeadRush Core: adapter hardware verification (#126)

The HeadRush adapter from #125 run against a physical HeadRush Core, criterion
by criterion. #126 says to run only the dedicated procedure for the ticket, so
nothing here explores: every check maps to an acceptance criterion and names it.

The procedure is `tools/verify_headrush.py`. Its stdout is this report's
evidence, quoted verbatim below.

It also carries the five-step pass @monzta1 asked for on #33 (2026-09-18),
including the two questions he left open. Those rows are tagged **`#33`**
rather than an `AC` so the ticket's criteria and his request stay separable.

**Scrubbed per AC7.** No rig name, rig id, setlist or preset content appears —
not in this document and not in the script's output, which redacts at the point
of printing rather than being edited into shape afterwards. Counts are recorded
because a count carries no library. The unit's `DeviceName` carries a per-unit
suffix and is reduced to the model.

**Only `##HRB` presets were touched.** Those are the test presets
@bschmalz81401 designates; everything else on the unit is a real rig. The script
refuses to run at all unless the loaded rig is one, and reloads it at the end.

## Unit (AC1)

| | |
|---|---|
| model | HeadRush Core |
| firmware | `5.1.0.2a63755` (`AppVersion`, read from the unit) |
| transport | unauthenticated HTTP on port 80, `/api/v1` |
| date | 2026-09-18 |

Prime and Flex Prime remain **UNVERIFIED** (AC8). The spec describes one engine
across the family; nothing here tests that and it is not assumed. The adapter
lists both as unverified and the run asserts it still does.

## The run

```
verifying against <host>, starting on a ##HRB preset

  [ok  ] AC1  firmware string read from the unit
         HeadRush Core, AppVersion '5.1.0.2a63755'
  [ok  ] AC1  matches the firmware the adapter claims evidence for
         adapter cites 5.1.0.2a63755, unit says 5.1.0.2a63755
  [ok  ] AC8  Prime and Flex Prime not claimed
         adapter lists them as unverified; nothing here tests them
  [ok  ] AC4  non-allowlisted object-method refused
         raised MethodRefused and the opener was not called (2 -> 2); deleteRig on /Evil/API/Rigs is not on this adapter's allowli...
  [ok  ] AC4  allowlist is deny-by-default and small
         only [('/Evil/API/Rigs', 'loadRig')] may be invoked
  [ok  ] AC2  current state reads back
         rig is a ##HRB preset, routing 0, 10 slots occupied
  [ok  ] AC2  current preset is named, not numbered
         current_preset() -> (None, <##HRB name>): a name, not a bank/patch number
  [ok  ] AC2  rig listing
         119 rigs, 18 are ##HRB test presets (names not recorded, AC7)
  [FAIL] AC2  rig selection through the adapter
         raised HTTPError: HTTP Error 504: Gateway Timeout
  [ok  ] AC2  unit returned to its starting rig
         restored directly, by rig id (name not recorded, AC7)
  [ok  ] AC4  ModuleType 20 refused before transport
         PermissionError raised and the opener was not called; Neural Amp Modeler 2: writing it killed the engine on a Co...
  [ok  ] AC2  topology selection is verified by read-back
         select_topology(1) -> read back 1; adapter reports ok=True
  [ok  ] AC2  topology restored
         back to routing 0
  [ok  ] AC2  representative parameter read
         Amp.Bass reads 0.41999998688697815 on the wire
  [ok  ] AC5  display read refuses rather than inventing a value
         NotMeasured: the wire-to-display curve is taper_id=None and the device does not say what that denotes, so it refuses (finding 3)
  [ok  ] AC3  write is followed by device read-back
         /Evil/Engine/Patch/Amp Bass = 0.25, read back 0.25; restored to 0.41999998688697815
  [ok  ] AC2  scene tri-state reads through the adapter
         scene 6 reads 11 slot entries; states seen: ['SceneSlotState.NO_CHANGE', 'SceneSlotState.OFF', 'SceneSlotState.ON']
  [ok  ] #33  set_scene engages the scene
         set_scene(first) -> ok=True, engaged=True, LastScene=5
  [ok  ] #33  set_scene engages a second, different scene
         set_scene(second) -> ok=True, engaged=True, LastScene=6
  [ok  ] #33  SceneActive is a latch, not a pulse (OPEN)
         first scene's flag was True while it was active; after engaging the second, the second reads True and the first reads False -> a LATCH the unit maintains and clears on change, not a pulse
  [ok  ] #33  scene restored
         back on the scene this pass started from
  [ok  ] #33  set_scene_slot writes both Effect and Mode
         a slot in scene 9 moved off -> on and read back on
  [ok  ] #33  scene slot restored
         back to off
  [ok  ] #33  set_bypass writes the block's On
         On forced to True, then set_bypass(slot, True) wrote On=False and read back False
  [ok  ] #33  bypass restored
         On back to the value this pass found it at (False)
  [ok  ] #33  place_block places a backed ordinal
         place_block(free slot, 19) -> Neural_Amp_Modeler in slot 6, read back and its object answers
  [ok  ] #33  place_block empties the slot again
         place_block(free slot, 0) -> slot 6 emptied
  [ok  ] #33  ordinal 4 is reported not placed, not falsely ok (OPEN)
         place_block(free slot, 4) -> ok=False: not placed: /Evil/Engine/Patch/Chain ModuleType6: wrote 4, unit reads 0 after 0.5 s; slot now holds 0
  [ok  ] #33  slot left empty
         the slot this pass used is empty again
  [ok  ] AC6  no storing method is on the allowlist
         allowlist is ['loadRig']; store, delete, rename and create are unreachable
  [ok  ] AC6  edit buffer discarded at the end
         dirty was True after the run's writes; the rig was reloaded by id, which discards them without storing

31 checks, 1 failed
```

The host is a LAN address and is redacted; it is supplied with `--host`.

## FAILURE: `select_preset()` loads a rig by name, and hardware wants the id

This is the one failing check, and it is a real defect in merged code. It is
recorded rather than worked around, per AC5.

`HeadrushAdapter.select_preset()` passes the rig **name** as `loadRig`'s first
argument. The unit answers `504 Gateway Timeout` and loads nothing. The same
call with the rig **id** returns `True` and loads.

```
isolating the two loadRig argument forms, on ##HRB test presets only
(rig names and ids not recorded, AC7)

  loadRig(name  <- what select_preset sends   ) -> HTTPError: HTTP Error 504: Gateway Timeout   [5.0s]
      unit now reports the requested rig: False
  loadRig(rig id <- what hardware accepts     ) -> returned True   [0.1s]
      unit now reports the requested rig: True
```

Both forms are available from the same object: `/Evil/API/Rigs` publishes
`AllRigNames` and `AllRigIds` as parallel lists, so a name-taking `select_preset`
can resolve one to the other without a second round trip.

### Why nothing caught it

- **`select_preset` has no test.** Nothing in `tests/` exercises the
  `HeadrushAdapter` method.
- **`devices/headrush/sim.py` does not implement `loadRig`.** There was nothing
  for a test to run against, so a test would have had to assert against a
  fabricated response, which is how the wrong argument would have been enshrined
  rather than caught.

The method therefore reached `main` and release 1.3.0 having never been
executed against anything. That is the case for the hardware gate: CI was
green, review was clean, and the method does not work on a device.

### A second issue in the same method, visible from the same evidence

`select_preset` reads `PresetName` back immediately. `loadRig` returns before
the engine has swapped the rig — the probe above sleeps 2s before its read for
exactly that reason, and the run's own restore path sleeps 1.5s. Even with the
id fix, the `ok` this method returns would be racing the device. The adapter's
parameter write path already has the right shape for this (`settle_s`, then
read back); rig selection does not use it.

### Not fixed here

#126 is verification and says to run only its procedure. The adapter is #125,
which is closed, and the fix is @monzta1's call — argument handling, settle, the
test and the sim's `loadRig` are design decisions in his component, not this
ticket's. Reported separately with this evidence.

## The #33 pass, and the two open questions it closes

@monzta1's #33 comment asked for five adapter behaviours to be exercised on a
unit, two of them phrased as open questions to be written down either way. All
five pass. The two open questions have answers.

| his step | result |
|---|---|
| 1. `select_topology(n)`, two routings, read-back | passes (recorded under AC2) |
| 2. `place_block(slot, 19)` then `(slot, 0)` | passes; ordinal 4 below |
| 3. `set_bypass(slot, True)` | passes |
| 4. `set_scene(n)` with `ModeNew{n} = 2` | passes |
| 5. `set_scene_slot(n, name, state)` | passes |

### OPEN QUESTION 1: `SceneActive{n}` is a latch, not a pulse

> "does `SceneActive{n}` stay True, or does the unit clear it once the scene
> applies?"

**It stays True, and the unit clears the *previous* scene's flag instead.**

```
first scene's flag was True while it was active; after engaging the second,
the second reads True and the first reads False
-> a LATCH the unit maintains and clears on change, not a pulse
```

This is measured across a **transition**, on purpose. Engaging one scene and
reading its own flag back cannot answer the question: a flag that reads `True`
immediately after its own write is equally consistent with a latch and with a
pulse that has not been cleared yet. Two scenes are needed — one to set, one to
displace it — and both halves are observed.

So the adapter's current caution can be tightened if @monzta1 wants: `set_scene`
reports `written` without requiring it, because the flag's persistence was
unmeasured. It is measured now, on this firmware, and the flag tracks the active
scene. `LastScene` remains the better success signal regardless — it is the
effect the write is *for* — so this is an option, not a defect.

**Bound:** one rig, one firmware, switches 6–9 in scene mode, `n = 1` per
transition. It is not established that the unit never pulses under some other
path (a physical footswitch press, a rig load mid-flight).

### OPEN QUESTION 2: ordinal 4 is reported not placed, and not falsely ok

> "4 is allowed on purpose, and the question is whether the delayed read-back
> reports 'not placed' as finding 1 predicts."

**It does.** The adapter allows the write, the device accepts it and silently
reverts to 0, and the 0.5 s settle catches it:

```
place_block(free slot, 4) -> ok=False: not placed:
  /Evil/Engine/Patch/Chain ModuleType6: wrote 4, unit reads 0 after 0.5 s;
  slot now holds 0
```

This is the failure mode the settle exists to prevent: an immediate read-back
would have seen `4` and returned a false `ok`. The check asserts both that
`ok is False` *and* that the slot really holds 0, so a refusal that lied in the
other direction would also fail.

The adjacent placements confirm the same path works when the ordinal is backed —
`place_block(slot, 19)` reports the module in the slot **and** its object
answering, and `place_block(slot, 0)` empties it again.

### What the #33 pass touched, and put back

Everything ran on the loaded `##HRB` test preset, in increasing order of how
much it perturbs the rig — scenes, then bypass, then chain edits — and each step
restores what it changed. Two of these checks were rewritten after a first run
passed them **vacuously**, which is worth recording because the passes looked
fine:

- `set_bypass` first landed on a block whose `On` was already `False`, so
  "wrote `On=False`, read back `False`" demonstrated no transition. It now
  forces the block on first.
- `set_scene` first ran with no scene engaged (the run's own mid-pass rig reload
  clears `LastScene`), so there was no previous flag to observe. It now engages
  two scenes in sequence.

A green check that could not have gone red is not evidence, and neither of those
would have caught a regression.

## What each criterion rests on

| | criterion | what proved it |
|---|---|---|
| AC1 | model and firmware string | read from the unit, and asserted equal to the firmware the adapter's own `evidence()` claims to have been verified against |
| AC2 | discovery, listing, selection, state, topology, scenes, reads | all pass **except rig selection**, above |
| #33 | @monzta1's five-step pass and its two open questions | all five pass; both questions answered above |
| AC3 | every tested write is followed by read-back | `Amp.Bass = 0.25` written and read back as `0.25`, then restored; `select_topology(1)` read back as `1`, then restored |
| AC4 | non-allowlisted method refused **before transport** | see below |
| AC5 | failures recorded, capability claims not weakened | the `select_preset` failure is recorded as a failure; `get_param_display` refuses rather than inventing a display value |
| AC6 | no store, reset, firmware or recovery | no storing method is on the allowlist at all; edit buffer discarded by reload |
| AC7 | scrubbed report | redaction happens in the script, at the point of printing |
| AC8 | Prime and Flex Prime unverified | asserted still listed as unverified; not tested |

### AC4: what "refused before transport" had to mean

Catching an exception proves the adapter raised. It does not prove nothing
reached the unit, which is what the criterion asks. So the client's opener is
wrapped in a counter and the check asserts the count is **unchanged** across the
refused call. A refusal that still opened a socket fails this check.

Two refusals are checked this way, and both hold with the opener untouched:

- `deleteRig` — not on the allowlist (`2 -> 2` calls).
- `ModuleType` ordinal **20** — the write that killed the engine on this
  firmware (`HEADRUSH-HARDWARE-FINDINGS.md`, finding 1). The refusal is checked;
  the crash is not reproduced.

### AC6: `dirty` is not the question

`dirty` means the edit buffer differs from what is on disk. It is true after any
write and false after a reload, so it cannot answer "was anything stored". What
answers it is that no storing method is reachable: `saveRig`, `saveRigAs`,
`deleteRig`, `makeNewRig` and `renameRig` are all off the allowlist, which is
`{("/Evil/API/Rigs", "loadRig")}` and nothing else. The run asserts that.

The unit is returned to its starting rig by reloading it by id, which discards
the run's writes without storing anything — the only restore route that does not
go through a storing method.

## An observation that is not a defect

`get_param_display` refuses for `Amp.Bass`, and the run records that as a pass,
because the adapter does not carry the taper table. `Amp.Bass` publishes no
`normalizeAlgo`, which #130 established means Linear — a conversion this repo
can now actually perform, via `devices/headrush/tapers.py`. So the adapter
currently refuses a conversion the repo has the data for.

That is a deliberate follow-up (wiring #130's table into `to_display()`), not a
fault in what was verified: refusing is the correct behaviour for a component
that has not been given the curve. Recorded so it is not mistaken for a finding
that display values are underivable in general — finding 3 says they are
underivable *from the device*, which is a different claim.

## What this run does not cover

- **One unit, one firmware, one run.** `n = 1` per check. Nothing here is a rate.
- **Prime and Flex Prime** (AC8) — untested, unclaimed.
- **Ordinal 20 was refused, never executed.** AC4 wanted the refusal, and the
  ordinal chosen for it is the one that killed the engine. Only 19, 4 and 0 were
  actually written to a slot.
- **Chain edits used one empty slot** on one rig. `place_block` is not
  exercised across the ordinal space, and `reorder_block` is not exercised at
  all.
- **Writes were exercised on one parameter.** `Amp.Bass`, chosen as
  representative of a continuous normalised control. Not a sweep.
- **No storing path was tested**, by AC6 and by design. Whether `saveRig` works
  is unknown here and deliberately so.

## Reproducing

With a `##HRB` test preset loaded on the unit and Remote enabled:

```
python tools/verify_headrush.py --host <unit>
```

Exit status is 0 when every check passes and 1 when any fails, so it can gate.
It refuses to start if the loaded rig is not a `##HRB` preset. If the unit
answers `403` on object paths, Remote is off; `404` on object paths with `GET /`
still answering is the engine having crashed (finding 2).
