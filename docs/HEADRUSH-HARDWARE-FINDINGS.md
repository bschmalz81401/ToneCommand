# HeadRush Core: hardware findings

Partial evidence for #126, which cannot be completed yet: that ticket declares
`Depends on: #125` and most of its criteria verify the finished adapter,
including AC4, refusing a non-allowlisted `object-method` before network
transport. No adapter exists, so what follows is the evidence that does not
depend on one, recorded now because one item is a safety finding that affects
work in flight.

Scrubbed per AC7. No rig library, setlist or preset contents appear. The unit's
`DeviceName` carries a per-unit suffix and is reduced to the model. One rig is
named, `##HRB ToneCommandTesting`, created for this work and holding nothing.

## Unit

| | |
|---|---|
| model | HeadRush Core |
| firmware | 5.1.0.2a63755 |
| transport | unauthenticated HTTP on port 80, `/api/v1` |
| date | 2026-09-15 |

Prime and Flex Prime are UNVERIFIED (AC8). The spec says one engine across the
family; nothing here tests that and it is not assumed.

## FINDING 1: a write of ModuleType ordinal 20 was followed by engine death

**What was observed, bounded to what was actually run.**

On this Core at firmware 5.1.0.2a63755, writing `20` to `Chain.ModuleType3` on an
empty test rig was accepted, read back as `20`, and followed by `/api/v1` going
down within the next two-second poll. The unit then reloaded itself and
presented a recovery prompt asking whether to load the last preset.

```
baseline API alive: True
write ModuleType3 = 20  ->  acknowledged, read back 20
  t+ 0s  API alive
  t+ 2s  API DOWN
```

That is enough to say: do not write ordinal 20. It is deliberately NOT written
up as "writing ordinal 20 crashes the engine", because the protocol does not
support a general law, and this document will be quoted as hardware fact.

### What the evidence does not cover

- **n = 1** for the isolated run. One controlled observation, not a rate.
- **The timing is a poll bin, not a latency.** Health was sampled every two
  seconds. What is known is that the unit was up at the first poll and down at
  the second, so death falls in (0, 2s]. "About two seconds" would be a
  measurement that was not made.
- **Slot 3 only, empty rig only.** The claim is about `Chain.ModuleType{n}`;
  the evidence is `n = 3` with nothing else placed.
- **Engine death and API unmount are not separated by this poll.** The probe
  recorded reachable or not. Finding 2 is a state where the unit still answers
  HTTP on `/` and 404s every API path, so "API DOWN" could be the crash, the
  unmount that follows it, or both in sequence. The recovery prompt is what
  distinguishes a fault from a hang, and that was seen on the unit by
  @bschmalz81401 rather than captured in the poll log.
- **The recovery performed is not shown to be the minimum.** The unit was power
  cycled and HeadRush Remote re-enabled. Whether the self-reload alone, or
  re-enabling Remote alone, would have sufficed was not tested.

### The earlier occurrence, written down rather than counted

An earlier session on the same test rig, which had an Amp in slot 2 at the
time, ran three writes and then found the API gone:

```
slot 3 before: 0   (slot 2 held ordinal 1, Amp)
wrote ModuleType3 = 20  -> read back 20
wrote ModuleType3 = 19  -> read back 19
wrote ModuleType3 = 0   -> read back 0
   ... every API path 404 on the next request, seconds later
```

This was previously described as a second reproduction. It is not one: three
writes in sequence cannot isolate any of them. It is consistent with the
isolated run and it is recorded here so a reader can judge it, rather than
summarised as a count.

### Ordinal 19, tested properly this time

An earlier version of this document cleared ordinal 19 on the grounds that
after the first crash the unit came up in a rig containing it. That is
load-from-disk, which is a different operation from writing `ModuleType = 19`
over HTTP, and it cleared nothing. Independent review and a self-audit both
caught it.

It has since been tested directly, as the same single-variable run used for
ordinal 20: same rig, same slot 3, same empty chain, write and read back, then
poll. Health was sampled every 0.5s rather than every 2s, and the probe
distinguished 403 from 404 rather than recording reachable or not.

```
baseline: alive
write ModuleType3 = 19  ->  acknowledged, read back 19
  alive for 30s
slot 3 restored to 0
```

**Writing ordinal 19 did not take the unit down.** So the pair is a controlled
comparison, which is worth more than either run alone:

| ordinal | name | object published | same rig, same slot, same protocol |
|---|---|---|---|
| 19 | Neural Amp Modeler | YES | alive 30s, no effect |
| 20 | Neural Amp Modeler 2 | NO | API gone inside the next poll |

Two adjacent roster entries naming the same module, differing in whether the
device publishes an object to address it.

WHAT THIS PAIR DOES AND DOES NOT SHOW. It rules out the reading that the NAM
module is dangerous to place, which was the live alternative at the time. It
does NOT support "roster entry with no object" as the class that matters: an
earlier draft said so here, and the next section is the testing that falsified
it. One pair is not a mechanism.

### All three unbacked ordinals tested, and they do three different things

The earlier version of this document proposed "roster entry with no object
path" as the class that mattered, having tested exactly one member of it.
Testing the other two falsified that. Same rig, same slot 3, same empty chain,
same protocol, health sampled every 0.5s:

Each write was followed by 30 seconds of health polling at 0.5s before the slot
was restored, so "no crash" means the unit was still answering 30s later, not
merely that the write returned.

| ordinal | name | object | what the write does | crash |
|---|---|---|---|---|
| 3 | ReValver Amp | present | sticks | no |
| **4** | **ReValver Amp 2** | **absent** | **silently reverts to 0 in ~0.4s** | **no** |
| 19 | Neural Amp Modeler | present | sticks | no |
| **20** | **Neural Amp Modeler 2** | **absent** | (engine died) | **YES** |
| 253 | C-Verb | present | sticks | no |
| **254** | **C-Verb 2** | **absent** | **sticks, with no object to address it** | **no** |

The three backed siblings are inert controls: written the same way, they take
the value and keep it.

**Unbacked does not predict a crash.** Only ordinal 20 does. The recommendation
to refuse all three, which was published to #125, rested on a class generalised
from the single member that had been tried. The CLASS was wrong. The
recommendation itself was conservative rather than false: it refused 4 and 254
as a judgement pending measurement, not as measured crashes, and said so. What
is retracted is the reason given for it, not a claim that those two crash.

#### Ordinal 4 is rejected by the device, silently

```
wrote 4 ->  t+0.04s slot 3 reads 4
            t+0.39s slot 3 reads 0
```

No error, no refusal, no crash. The write is acknowledged, the read-back agrees
for about four tenths of a second, and then the unit puts the slot back to
empty on its own. Its backed sibling, ordinal 3, written identically in the same
slot moments earlier, stays put.

#### Ordinal 254 is accepted and is not addressable

It sticks at 254 and the unit stays up. But `/Evil/Engine/Patch/C-Verb_2` is
still absent (404) while the block is placed, and `EffectType3` reads 0. So the
slot holds something with no object through which any parameter of it could be
read or written.

### What this means for verification

Stronger than the earlier write-up, and more precisely: read-back of the written
value is not sufficient, but it fails differently in each case and one of them
it does not fail at all.

    ordinal 20   read-back agrees, then the engine dies. No read of the
                 ModuleType catches this, prompt or delayed, because the value
                 is not what went wrong.

    ordinal 4    an IMMEDIATE read-back agrees and is wrong: the device has
                 discarded the write by t+0.39s. A read taken after a short
                 delay sees 0, which is the honest answer, so a delayed
                 re-read DOES catch this one.

    ordinal 254  read-back of the value is correct and stays correct. This is
                 not a read-back failure. The slot genuinely holds 254; what is
                 missing is the object, so the check that catches it is object
                 presence (`/Evil/Engine/Patch/C-Verb_2` is 404 while the block
                 is placed), not any re-read of the ordinal.

So an adapter needs three different things, and an earlier version of this
section flattened them into "read-back does not catch any of these", which is
false for 4 and misdirects on 254: it would send someone to delayed re-reads for
a problem no re-read of that value can see.

### What this suggests for #125, revised

The earlier recommendation was "refuse all three". On the evidence that is
wrong, and here is what the measurements actually support:

- **Ordinal 20: refuse.** It is the one measured unit-down, and read-back does
  not catch it.
- **Ordinal 4: no refusal is required, but a prompt read-back is not enough.**
  The device rejects it itself, promptly and without harm. An adapter that
  verified after a short delay would see the slot empty, which is the correct
  outcome arrived at honestly; one that verified immediately would report
  success for a write that is already gone.
- **Ordinal 254: refuse it too, for a different and much weaker reason.** It
  does not crash, and the value reads back correctly; it occupies a slot with
  no object to address, so nothing downstream can set a parameter on it. The
  check that detects it is object presence, not a re-read of the ordinal.

Whether some property shared by 20 and not by 4 or 254 explains the crash is
unknown. NAM is the capture engine and the other two are not, which is a
hypothesis and is explicitly not a finding; this document has already been
wrong once by promoting exactly that kind of guess.

## FINDING 2: Remote gates the API, and its signature is 403, not 404

Measured by toggling HeadRush Remote on the unit with no reboot, in both
directions, 2026-09-15. An earlier version of this document inferred the gating
from the editor's own connection-failure dialog and got the SIGNATURE wrong,
which is recorded below because the wrong version shipped a diagnostic that
pointed operators at the wrong thing.

| request | Remote OFF | Remote ON |
|---|---|---|
| `GET /` | 200 | 200 |
| `/api/v1/subtree/{path}` | 404 | 200 |
| `/api/v1/object-properties/{path}` | **403** | 200 |
| `/api/v1/object-meta/{path}` | **403** | 200 |

With Remote off the unit says why, in the body:

```json
{"desc": "DataModel: Web access temporarily disabled",
 "reason": "Forbidden", "status": 403}
```

Turning Remote back on restored 200 on every path immediately, without a
reboot. So Remote does gate the API, that is now measured rather than inferred,
and the state is entirely recoverable by the operator.

### The correction, and why it mattered

After each crash in finding 1, `object-properties` returned **404**. With Remote
off it returns **403**. Those are different failures:

    403 on an object path   Remote is off. Turn it on. No reboot needed.
    404 on an object path   this firmware has no such path, OR the engine is
                            not running. Remote is NOT the problem.

The earlier version of this document, and the `describe_unreachable` text that
went with it, told anyone seeing a 404 to check HeadRush Remote. A 404 is
precisely the case where Remote is demonstrably fine, so that advice sent an
operator to the one thing that was not wrong.

The mistake was building on the editor's dialog, which names Remote for EVERY
connection failure because it is generic advice rather than a diagnosis. It was
labelled as inferred, which was honest, and it still pointed the wrong way. One
toggle settled it, and the toggle is what should have happened before the advice
was written.

### What this also says about finding 1

The post-crash state was not Remote being switched off. `object-properties`
404ing rather than 403ing means the engine itself was not serving, which is
consistent with a crash and is a stronger reading of finding 1 than the earlier
write-up allowed: the two failures are now distinguishable and the crash was
the other one.

It does not change what recovery was performed. The unit was power cycled and
Remote re-enabled together, so whether either alone would have sufficed is still
untested. Given Remote-off is a 403 and the observed state was 404, Remote was
probably not what needed re-enabling.

## FINDING 3: continuous parameters are normalised on the wire

The wire takes `0..1`. `minimum`, `maximum` and `format` describe the scale the
unit SHOWS. Measured by writing a value and reading the unit's own screen:

| parameter | wire | screen | published range |
|---|---|---|---|
| Amp.Bass | 0.75 | 75 % | 0..100 |
| Amp.Treble | 0.5 | 50 % | 0..100 |
| Amp.PostGain | 0.5 | 0.0 dB | -12..12 |
| Amp.TremDepth | 0.0 | 0 % | 0..100 |
| Amp.TremSpeed | 0.5 | 5.19 Hz | 0.25..20 |
| Amp.TremSpeed | 0.25 | 1.48 Hz | 0.25..20 |

The API alone cannot establish this: the device accepts a write of either `0.75`
or `75` to `Amp.Bass` and clamps neither. The screen is what settles it.

The last two rows are not linear. The device publishes an opaque id for the
curve (`x-options.normalizeAlgo`) and no formula, so the readings above are
recorded here as MEASUREMENTS and nothing is decoded from them.

Decoding the curves is a separate piece of work and a different provenance,
read out of the vendor's editor rather than measured: that is #130, and nothing
in this commit implements it. What belongs here is only the six readings, which
are what a later decode has to reproduce.

## FINDING 4: the simulator's scene model is correct

The first check in this file that CONFIRMS something rather than correcting it.

`devices/headrush/sim.py` models scene slots as `{0: no_change, 1: on, 2: off}`.
That constant is on `main`, phases 4 and 5 will be written against it, and it
had never met a unit. If it were inverted, every scene an adapter wrote would be
inverted, and the simulator would agree with the adapter all the way down
because they share the constant.

It is correct. Measured across four scenes on a rig that actually uses them.

### What the schema does and does not settle

`Scene{n}_{m}_Mode` publishes no option names: `integer`, 0..2, nothing else. So
the mapping is not readable from the device's self-description.

Its siblings on the same object do publish names, and all three put "No Change"
at index 0:

    SceneDoubleSwitch{n}   ['No Change', 'A', 'B']
    ScenePathSwitch{n}     ['No Change', 'A', 'B']
    SceneExtAmp{n}         ['No Change', 'None', 'Tip', 'Ring', 'Both']

That is convention within one object, which is a reason to expect 0 to mean no
change and not a reason to claim it.

### 1 and 2, measured

Each scene was engaged, then every Mode 1 or 2 slot was compared against that
block's own `On` property. Counted by script rather than by eye, after an
earlier draft of this section reported the total wrong:

    scene 6    9 predictions
    scene 7    9 predictions
    scene 8   10 predictions
    scene 9   10 predictions
    TOTAL     38 predictions, 0 wrong

`1` is on and `2` is off.

### 0, measured in both directions

One observation is not enough here, and an earlier version of this section
stopped at one.

A block at Mode 0 that happens to be off is equally consistent with `0` meaning
off, so the test has to make the block hold a value the previous scene forbade.
Do only that, and the block ends ON, which is equally consistent with `0`
meaning ON and the scene writing a value the slot already held. The 38 Mode 1/2
predictions never constrained Mode 0, so they do not close it either.

Both directions, on the same scene and the same block. `Black Wah` is Mode 2 in
scene 8 and Mode 0 in scene 6, and scene 6 declares Mode 1 or 2 for nine other
slots, which move on each engage and show the scene acted:

    scene 8 engaged      Black Wah forced off by its own Mode 2
    turned ON by hand    holding a value scene 8 forbids
    scene 6 engaged      nine others move; Black Wah stays ON
                         -> 0 is not OFF

    scene 8 engaged      Black Wah on, then turned OFF by hand
    scene 6 engaged      nine others move; Black Wah stays OFF
                         -> 0 is not ON

Neither result alone identifies `no_change`; together they do. The unit declines
to touch the slot rather than writing anything to it.

Caught by independent review, which pointed out that the one-directional version
left "actively declines to touch" unsupported while this document was being
cited elsewhere as the evidence base for the tri-state.

### Scene activation IS on the API, and two earlier claims here were wrong

Writing `SceneActive{n} = true` engages scene `n` and applies its whole table,
provided that switch is in scene mode (`ModeNew{n} = 2`). Confirmed on four
scenes; the 38 predictions above were all taken on scenes engaged this way.

An earlier version of this finding said activation was not on the API at all.
That came from writing `SceneActive` on a BLANK test preset where `ModeNew` was
0 on every switch, so there was no scene to engage, and then, after setting
`ModeNew1 = 2`, trying `FootswitchHeld` and `FootSwitchOn` and never retrying
`SceneActive`. An absence concluded from a test that could not have shown the
presence.

`bschmalz81401/HeadrushRigBuilder` had this measured and documented correctly on
2026-09-07, including the same dependency on `ModeNew`. It was not consulted.

### The index is consistent; only LastScene is zero based

    ModeNew{n}   FootSwitchText{n}   SceneActive{n}   Scene{n}_{m}_Mode
    all share the same n

    LastScene = n - 1

Measured by engaging scenes 6, 7, 8 and 9 and reading back: `LastScene` was 5,
6, 7 and 8. The label (`FootSwitchText{n}`) is free operator text and on this
rig reads "SCENE 1" through "SCENE 4" on switches 6 through 9, so it is the one
number that carries no relationship at all.

AN EARLIER VERSION OF THIS FINDING CLAIMED FOUR DIFFERENT NUMBERS FOR ONE
SCENE, with the footswitch index one below the `SceneActive` index. That was
wrong. It rested on a property read taken WHILE a rig was loading: the labels in
that read were shifted by one against the ones the same rig reports when
settled, and `loadedName` came back empty in the same response, which was
noticed at the time and not treated as the warning it was.

The practical lesson is narrower than the wrong claim was: a read taken during
a load can mix rigs, and a scene table is exactly the shape where that is
invisible. Settle before reading, or check `loadedName` is non-empty first.

### Method calls do work, and return values

Separately established while setting this up: `loadRig(<rig id>, "")` on
`/Evil/API/Rigs` loads a rig and returns `True`. That is the first
`object-method` call this project has made on hardware, and it matters for #125
beyond rig loading: the method surface returns meaningful values rather than
only 200 or 504, so an allowlisted method can be verified by its return. Given
how poorly read-back performed in finding 1, that is worth knowing.

## Not verified, and why

| AC | status |
|---|---|
| 1. model and firmware | done, above |
| 2. discovery, rigs, topology, scenes, reads, verified writes | PARTIAL. The ADAPTER cannot be verified without #125. The device behaviour behind three of these now has been: rig SELECTION (`loadRig`, finding 4; listing is not shown), scene tri-state (finding 4), and parameter reads (finding 3). Topology selection and verified writes are still only #109's per-slot result and finding 1 |
| 3. read-back after each write | PARTIAL. Every write in this session was read back. The AC2 write set was not run, because there is no adapter. AC3 is a procedure requirement, not a claim that read-back detects crashes; the argument that it does not is under finding 1 |
| 4. non-allowlisted object-method refused before transport | BLOCKED, the allowlist is #125 |
| 5. record failures rather than weaken claims | done, findings 1 and 2 |
| 6. no destructive operations | no store, reset, firmware or recovery operation was invoked. The unit was nonetheless taken down twice and presented a recovery prompt, and writing an unbacked ordinal until the engine died is arguably outside "the dedicated hardware verification procedure" this ticket asks for. Recorded rather than claimed as clean |
| 7. scrubbed report | this document |
| 8. Prime and Flex Prime unverified | honoured, stated above |

Nothing was stored, reset, or flashed. Every write went to the edit buffer of
`##HRB ToneCommandTesting`, a rig created for this work, and the two crashes
confirmed the point by discarding everything: the unit came back with the rig as
it is on disk, empty.

---

## Finding 5: a continuous parameter does not hold the value you wrote

Measured 2026-09-19 and 2026-09-20 on the same Core at `5.1.0.2a63755`, on a
`##HRB` preset. Filed as #167, fixed in #173. This section is the device fact;
the adapter's handling of it is in `devices/headrush/adapter.py` and the run
transcripts are in `HEADRUSH-DISPLAY-VERIFICATION-168.md`.

**The unit converts a written wire value to display, snaps the DISPLAY value
to the parameter's published `x-options.grid`, converts back through the same
curve, and stores the result as float32.**

So a read-back differs from the write whenever the display value it produced
was not already on the grid. Read-back verification by exact equality against
what was sent therefore reports failure for writes the device honoured — on
`Amp.TremSpeed`, every wire value tested.

    stored = to_wire( snap_to_grid( to_display(wire) ) )     # float32 out

Eight write/read pairs, two curves, three grids:

| property | curve | grid | wrote | unit held |
| --- | --- | --- | --- | --- |
| `Amp.TremSpeed` | Squared | 0.01 | `0.5` | `0.5001265406608582` |
| `Amp.TremSpeed` | Squared | 0.01 | `0.25` | `0.24955657124519348` |
| `Amp.TremSpeed` | Squared | 0.01 | `0.3333333` | `0.33299562335014343` |
| `Amp.Bass` | Linear | 1.0 | `0.5` | `0.5` |
| `Amp.Bass` | Linear | 1.0 | `0.25` | `0.25` |
| `Amp.Bass` | Linear | 1.0 | `0.3333333` | `0.33000001311302185` |
| `Amp.PostGain` | Linear | 0.1 | `0.5` | `0.5` |
| `Amp.PostGain` | Linear | 0.1 | `0.3333333` | `0.3333333432674408` |

The formula reproduces all eight bit-exactly. `test_the_prediction_reproduces_
every_pair_measured_on_the_unit` asserts it.

### 5a. The published grid is itself a float32

A grid the vendor wrote as `0.01` arrives as `0.009999999776482582`. Snapping
with that literal lands beside the mark the unit uses:

    grid used literally   -> Amp.TremSpeed predicted 0.33299559354782104
    unit actually holds                              0.33299562335014343

`tapers.snap_to_grid` recovers the decimal at float32's ~7 significant digits.
Two versions of the #173 fix got this wrong in two different ways and neither
looked wrong; only the pairs above caught it.

### 5b. A write fails iff the display value it produces is off the grid

#167's body said "every write on a quantized parameter reports failure". That
is broader than the device. `set_param_display(5.19)` on `Amp.TremSpeed`
reported `ok=True` on hardware, because `5.19` is already on the 0.01 grid and
the unit had nothing to snap. An on-grid write is stored exactly, so it can and
should still be verified exactly.

### 5c. Every value the device holds is a fixed point

Writing back a value the unit is already holding reads back identical: it is by
construction the image of a grid point. The corollary was measured — restoring
`Amp.TremSpeed` to the `0.5` it held wrote `0.5001265406608582`, and **no wire
value returns it to `0.5`**. Read-modify-restore code must treat "same
displayed value" as success rather than bit equality.

### 5d. A simulator that stores writes verbatim cannot see any of this

`HeadrushSim` did, which is why the whole suite was green while a real Core
reported failure for honoured writes: the double was modelling a device that
does not quantize. It now performs the same convert-snap-convert and reproduces
all eight values above. **Agreement between the simulator and the adapter is
therefore self-consistency, not evidence** — both use the same vendor table.
The evidence is the hardware pairs.

### Provenance

The `ok` flags, read-back floats and restore behaviour above are the device's
own HTTP responses. Displayed values quoted in the run transcripts were read
from the vendor web editor, which the owner attests matches the device — and
which is not independent evidence about the curves, because
`devices/headrush/tapers.py` was extracted from that editor's own bundle.
