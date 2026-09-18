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
to refuse all three, which was published to #125, rested on generalising from
the single member that had been tried, and two thirds of that generalisation is
now wrong.

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
