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

### Ordinal 19 is NOT cleared

An earlier version of this document said the NAM module itself is fine, on the
grounds that after the first crash the unit came up in a rig containing ordinal
`19`. That is load-from-disk, which is a different operation from writing
`ModuleType = 19` over HTTP, and it does not clear the write. Ordinal 19 was
written over HTTP exactly once, in the mixed sequence above, which cannot clear
it either.

So: writing 20 was sufficient to take the unit down. Whether writing 19 is safe
is UNTESTED. That correction matters because the refusal list below was
published to #125 as though it were settled.

### The class these three belong to

Three roster entries have a `ModuleType` ordinal and no object at the
corresponding path. That is a schema fact, checkable without hardware:

| ordinal | name | object | crash |
|---|---|---|---|
| 4 | ReValver Amp 2 | absent | NOT TESTED |
| 20 | Neural Amp Modeler 2 | absent | observed once, isolated |
| 254 | C-Verb 2 | absent | NOT TESTED |

The honest description of the class is "roster name with no object path", and
nothing more. An earlier version explained it by the unit's one Capture and one
C-Verb per rig rule, which does not cover ReValver Amp 2, since ReValver is not
a Capture. And a trailing ` 2` is not itself the problem: `Amp 2` is a roster
entry WITH an object at `/Evil/Engine/Patch/Amp_2`, and is not implicated.

**4 and 254 were not tested, and the reason is cost, not confidence.** Testing
one costs a crash and a power cycle on someone's hardware. Refusing them is not
free either: it means an adapter can never select those two roster entries, and
whether they work is simply unknown.

### What this suggests for #125

A conservative write path would refuse ordinal 20 on the evidence, and refuse 4
and 254 as a JUDGEMENT pending measurement, on the grounds that the one member
of that schema class anybody has written took the unit down. That is a policy
call for the maintainer, not a measurement, and it should not be described as
being in the same class as the never-brick guard, which covers firmware, store
and recovery operations.

What the evidence does support without qualification is narrower and still
useful: **read-back verification does not detect this.** The write was
acknowledged, the read-back agreed, and the engine died afterwards. Any
verified-write built on read-and-compare would report this write as a success.

## FINDING 2: the API was unreachable while the unit still served its editor

After each crash `/api/v1` returned 404 on every path while the unit kept
serving its editor's static files, and it did not recover on its own.

THAT REMOTE IS THE CAUSE IS INFERRED, NOT TESTED. HeadRush Remote was never
toggled while the API was watched. The evidence is the observed 200/404 split
plus the editor's own advice, and that advice is its GENERIC connection-failure
message rather than a diagnosis of this state. The unit was later power cycled
and Remote re-enabled together, so which of the two restored the API is also
unknown. The state is real and diagnosable either way; the mechanism is a
hypothesis worth one toggle to settle.

The failure is specific and diagnosable, because the unit keeps serving its
editor's static files the whole time:

```
GET /                     200    the editor page loads
GET /api/v1/subtree       404    every API path
```

The unit's own editor detects exactly this and says so: "Connection cannot be
established to your HeadRush device. Check that HeadRush Remote is active on
your HeadRush device."

The unit also announces the crash itself. @bschmalz81401, watching it: after
either crash it reloads on its own and comes up asking whether to load the last
preset, yes or no. That is a recovery prompt rather than a normal boot, which is
corroboration that this is a fault and not a hang, and it is operationally
relevant: anyone reproducing finding 1 has to answer a dialog on the unit before
the API is reachable again, on top of re-enabling Remote.

`describe_unreachable()` in `devices/headrush/client.py` currently explains name
resolution and connection failures, which are the wrong advice here: the name
resolved, the connection succeeded, and the unit answered. A 200 on `/` with a
404 on `/api/v1` means the unit is up and Remote is off, and saying so is worth
more than telling someone to check their Wi-Fi.

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

## Not verified, and why

| AC | status |
|---|---|
| 1. model and firmware | done, above |
| 2. discovery, rigs, topology, scenes, reads, verified writes | BLOCKED, verifies the adapter from #125 |
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
