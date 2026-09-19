# HeadRush Core: adapter hardware verification (#126)

The HeadRush adapter from #125 run against a physical HeadRush Core, criterion
by criterion. #126 says to run only the dedicated procedure for the ticket, so
nothing here explores: every check maps to an acceptance criterion and names it.

The procedure is `tools/verify_headrush.py`. Its stdout is this report's
evidence, quoted verbatim below.

It also carries the five-step pass @monzta1 asked for on #33 (2026-09-18),
including the two questions he left open. Those rows are tagged **`#33`**
rather than an `AC` so the ticket's criteria and his request stay separable.

**Scrubbed per AC7.** No rig name, rig id, setlist or preset content appears  - 
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
  [ok  ] AC4  no destructive method is on the allowlist
         allowlist is ['loadRig']; none of ['deleteRig', 'factoryReset', 'makeNewRig', 'renameRig', 'saveRig', 'saveRigAs', 'updateFirmware'] appears
  [ok  ] AC4  allowlist is deny-by-default and small
         only [('/Evil/API/Rigs', 'loadRig')] may be invoked
  [ok  ] AC4  ordinal 20 is in the refusal table
         the engine-killing ordinal is refused by data, checked without writing it
  [ok  ] AC4  non-allowlisted object-method refused before transport
         MethodRefused: refused; opener went 3 -> 3
  [ok  ] AC4  nothing reached the unit during that refusal
         opener call count unchanged (3 -> 3)
  [ok  ] AC4  the adapter is holding the wrapped client
         the counter is in the path the adapter actually uses
  [ok  ] AC2  current state reads back
         rig is a ##HRB preset, routing 0, 10 slots occupied
  [ok  ] AC2  current preset is named, not numbered
         current_preset() -> (None, <##HRB name>): a name, not a bank/patch number
  [ok  ] AC2  rig listing
         120 rigs, 19 are ##HRB test presets (names not recorded, AC7)
  [ok  ] AC2  rig selection through the adapter
         adapter loaded the requested test preset and it read back after polling; adapter itself reported ok=True
  [ok  ] AC2  unit returned to its starting rig
         restored directly, by rig id (name not recorded, AC7)
  [ok  ] AC4  refused ModuleType blocks before transport
         PermissionError: ordinal 254 refused; opener went 24 -> 24. 20 is refused by the same table, asserted above without writing it
  [ok  ] AC4  nothing reached the unit during that refusal
         opener call count unchanged (24 -> 24)
  [ok  ] AC2  topology selection is verified by read-back
         routing moved 0 -> 1 and read back; adapter reports ok=True
  [ok  ] AC2  topology restored
         wanted routing 0, unit reads 0; the restoring write reported ok=True (/Evil/Engine/Patch/Chain Routing = 0, read back 0)
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
         back on the scene this pass found engaged at entry
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
         dirty was True after the run's writes and reads False now; the rig was reloaded by id, which discards them without storing

36 checks, 0 failed
```

The host is a LAN address and is redacted; it is supplied with `--host`.

## The `select_preset` defect, and its fix, verified

The first run of this procedure failed one check. `HeadrushAdapter.select_preset()`
passed the rig **name** as `loadRig`'s first argument; the unit answered
`504 Gateway Timeout` and loaded nothing, where the rig **id** returned `True`
and loaded:

```
loadRig(name  <- what select_preset sent    ) -> HTTPError: HTTP Error 504: Gateway Timeout   [5.0s]
      unit now reports the requested rig: False
loadRig(rig id <- what hardware accepts     ) -> returned True                                [0.1s]
      unit now reports the requested rig: True
```

Nothing caught it because `select_preset` had no test and
`devices/headrush/sim.py` did not implement `loadRig`, so it reached `main` and
1.3.0 having never been executed against anything.

Fixed in #135 (`90838ff`): the name is resolved to an id through the parallel
lists, the id is sent, the read-back waits, and the simulator implements
`loadRig` and reproduces the 504 for a name so the path is executed under test.
**The run above is against that fix, and the check passes.**

### How long a rig load actually takes

`loadRig` returns before the engine swaps, so the read-back has to wait for
something. Measured on this unit, over 16 loads:

```
poll cost (one GET PresetName): median 10 ms   <- the measurement's resolution

loadRig returns at   t+144 .. 278 ms
PresetName swaps at  t+159 .. 679 ms   (median 376 ms)
```

A fixed settle of 0.5 s after the call reads at `t+644 .. 778 ms`. Over those
16 loads the swap landed after that read **once**, with a worst margin of
**-10 ms**: it passes by accident rather than by design.

The slow tail is not predictable. It is not the rig and not a cold/warm effect
- the same rig swapped at 173 ms and at 526 ms on different loads, and loading
one twice in a row was no cheaper:

```
alternating A B A B A B ->  526, 163, 294, 397, 197, 170 ms
same rig twice in a row ->  189, 178 ms
```

So a larger constant moves the flake rather than removing it, and makes the
common case slower. This procedure therefore **polls** for the rig rather than
sleeping (`wait_for_rig`), which returns as soon as the engine has swapped and
turns an intermittent false negative into a real timeout when a load genuinely
fails. Reported on #134 for the adapter to do the same if @monzta1 wants it;
that is his component's call.

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
pulse that has not been cleared yet. Two scenes are needed - one to set, one to
displace it - and both halves are observed.

So the adapter's current caution can be tightened if @monzta1 wants: `set_scene`
reports `written` without requiring it, because the flag's persistence was
unmeasured. It is measured now, on this firmware, and the flag tracks the active
scene. `LastScene` remains the better success signal regardless - it is the
effect the write is *for* - so this is an option, not a defect.

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

The adjacent placements confirm the same path works when the ordinal is backed  - 
`place_block(slot, 19)` reports the module in the slot **and** its object
answering, and `place_block(slot, 0)` empties it again.

### What the #33 pass touched, and put back

Everything ran on the loaded `##HRB` test preset, in increasing order of how
much it perturbs the rig - scenes, then bypass, then chain edits - and each step
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
| AC2 | discovery, listing, selection, state, topology, scenes, reads | all pass, rig selection included since #135 |
| #33 | @monzta1's five-step pass and its two open questions | all five pass; both questions answered above |
| AC3 | every tested write is followed by read-back | `Amp.Bass = 0.25` written and read back as `0.25`, then restored; `select_topology(1)` read back as `1`, then restored |
| AC4 | non-allowlisted method refused **before transport** | see below |
| AC5 | failures recorded, capability claims not weakened | the `select_preset` failure was recorded as a failure rather than worked around, and is what produced #135; `get_param_display` refuses rather than inventing a display value |
| AC6 | no store, reset, firmware or recovery | no storing method is on the allowlist at all; edit buffer discarded by reload |
| AC7 | scrubbed report | redaction happens in the script, at the point of printing |
| AC8 | Prime and Flex Prime unverified | asserted still listed as unverified; not tested |

### AC4: what "refused before transport" had to mean

Catching an exception proves the adapter raised. It does not prove nothing
reached the unit, which is what the criterion asks. So the client's opener is
wrapped in a counter and the check asserts the count is **unchanged** across the
refused call. A refusal that still opened a socket fails this check.

That counter is a complete answer only if every byte leaves through that one
opener, so the run asserts the adapter is holding the wrapped client.
`HeadrushAdapter` performs no I/O of its own and reaches the network only
through its client, and `HeadrushClient` has exactly one outbound call site.

### A safety check must not be the thing it checks for

The first version of this procedure probed AC4 by calling `deleteRig` and by
writing `ModuleType` ordinal **20**, the ordinal that killed the engine. Both
are safe only if the interlock works, which is the thing under test. A broken
allowlist would have deleted a rig; a broken refusal table would have written
the crash ordinal, to a hardcoded slot that is occupied on a real rig. The
probe was the catastrophe it was checking for. Caught in review of #134.

It is now split so a failure of the mechanism cannot execute the dangerous
operation:

| | checked how |
|---|---|
| `deleteRig`, `saveRig`, `factoryReset`, `updateFirmware` and friends are not reachable | **as data.** Asserted absent from the allowlist. Nothing is called. |
| ordinal 20 is refused | **as data.** Asserted present in the refusal table. It is never written. |
| a non-allowlisted method is refused before transport | with a method name **no device implements**. A broken allowlist gets a 404, not a deletion. |
| a refused ordinal is blocked before transport | with ordinal **254**, which finding 1 measured as sticking without an object rather than crashing, written to a slot **measured empty on this rig** rather than a hardcoded one. |

The transport claim still rests on the opener counter; only the operands
changed, from ones that would be destructive if the guard failed to ones that
would not.

### AC6: `dirty` is not the question

`dirty` means the edit buffer differs from what is on disk. It is true after any
write and false after a reload, so it cannot answer "was anything stored". What
answers it is that no storing method is reachable: `saveRig`, `saveRigAs`,
`deleteRig`, `makeNewRig` and `renameRig` are all off the allowlist, which is
`{("/Evil/API/Rigs", "loadRig")}` and nothing else. The run asserts that.

The unit is returned to its starting rig by reloading it by id, which discards
the run's writes without storing anything - the only restore route that does not
go through a storing method.

## AC7 is enforced at print time, and was not before

The report claims the transcript is committed verbatim rather than edited into
shape. In the first version that claim was **false**: redaction happened at the
call sites, the host was printed unredacted, and the `<host>` in the committed
transcript was put there afterwards by a `replace()`. Caught in review of #134.

`Report.record` now runs every line through `redact()` before printing, seeded
with the host and the unit's rig names before the first line of output. A host
or rig name arriving inside an exception message cannot reach the transcript
either, which was the concrete hole: an `HTTPError` carries the url.

## An observation that is not a defect

`get_param_display` refuses for `Amp.Bass`, and the run records that as a pass,
because the adapter does not carry the taper table. `Amp.Bass` publishes no
`normalizeAlgo`, which #130 established means Linear - a conversion this repo
can now actually perform, via `devices/headrush/tapers.py`. So the adapter
currently refuses a conversion the repo has the data for.

That is a deliberate follow-up (wiring #130's table into `to_display()`), not a
fault in what was verified: refusing is the correct behaviour for a component
that has not been given the curve. Recorded so it is not mistaken for a finding
that display values are underivable in general - finding 3 says they are
underivable *from the device*, which is a different claim.

## FINDING: a rig is not loaded when `loadedName` says it is

This started as an unexplained transient: the `topology restored` check failed
once, could not be reproduced, and was written up as unexplained rather than
dismissed. It then failed again, and because the check's message had been
changed to print the *observed* value rather than the wanted one, the second
failure said what was actually happening:

```
[FAIL] AC2  topology selection is verified by read-back
       routing moved 0 -> 0 and read back; adapter reports ok=False
[FAIL] AC2  topology restored
       wanted routing 0, unit reads 1; the restoring write reported ok=False
       (/Evil/Engine/Patch/Chain Routing: wrote 0, unit reads 1 after 0.5 s)
```

The readings are inverted, not stale, which rules out a slow write. `Routing`
was measured directly and is fast: **read back in 17..38 ms over 14 writes**,
never anywhere near the adapter's 0.5 s settle.

What is actually happening is that the topology write was landing **during a
rig load that had already reported itself finished**. `loadedName` flips early:

```
trial 1: loadedName flipped at t+332 ms, and the chain was STILL THE PREVIOUS
         RIG'S (9 modules). It became the new rig's (4 modules) at t+1392 ms.
trial 2: loadedName flipped at t+195 ms, chain already stable.
trial 3: loadedName flipped at t+185 ms, chain changed at t+1140 ms.
```

**The name flips 185..332 ms after `loadRig`, and the chain can keep being
rebuilt for about a second after that.** A write issued in that window races
the tail of the load and loses: the unit installs the rig's own stored value
over it, and the read-back reports a mismatch that is not the writer's fault.

That also explains why it was intermittent. It only bites when a write closely
follows a rig load, which is this procedure's order, and only when that load
happens to be one of the slow ones.

### What this procedure does about it, and the wrong fix first

The first attempt waited for **quiescence**: the chain identical across
consecutive samples. That is not sufficient, and the reason is in the
measurement above. **The previous rig's chain is quiet too.** It was measured
sitting unchanged for roughly a second after the name flipped, which is longer
than any sensible quiet window, so waiting for stillness can succeed on the old
chain and return exactly as early as not waiting at all.

`wait_for_rig` therefore waits for three things:

1. `loadedName` matching the requested rig.
2. The chain no longer being the shape captured **before** the load.
3. That shape holding still across three consecutive reads, 250 ms apart.

Condition 2 needs the caller to snapshot first, so every load site here takes a
`chain_shape()` before calling `loadRig`. Two rigs can share a chain, and
reloading a rig certainly does, so a shape that never differs is not treated as
an error: `REBUILD_CEILING_S` (2.5 s, against a longest measured rebuild of
1392 ms) is waited out instead.

Three consecutive full runs pass with this in place, where the previous build
failed two of five.

### What it means beyond this procedure

Any code that loads a rig and then writes has this race, including
`select_preset`, which reads `PresetName` back and reports success on it.
Reported on #134 for @monzta1 to decide; the adapter is his component and the
right fix there may be different from the right fix here.

## What this run does not cover

- **One unit, one firmware.** `n = 1` per check unless a number is given.
  The timing measurements state their own sample counts; nothing else here
  is a rate.
- **Prime and Flex Prime** (AC8) - untested, unclaimed.
- **Ordinal 20 is asserted as data and never reaches `place_block` at all.**
  The transport refusal is probed with ordinal **254**, which finding 1
  measured as harmless. The ordinals actually written to a slot are 19, 4 and
  0. Nothing here re-tests that 20 kills the engine, and nothing should.
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
