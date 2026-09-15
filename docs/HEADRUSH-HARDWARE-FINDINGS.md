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

## FINDING 1: ModuleType ordinal 20 crashes the engine

**Severity: remote crash, reachable from a single documented property write.**

Writing `20` to `Chain.ModuleType{n}` takes the unit down. The engine dies about
two seconds later and the whole `/api/v1` surface stops answering until the unit
is power cycled and HeadRush Remote re-enabled.

Reproduced twice, the second time as a single-variable test from a clean
baseline on an empty test rig:

```
baseline API alive: True
write ModuleType3 = 20  ->  acknowledged, read back 20
  t+ 0s  API alive
  t+ 2s  API DOWN
```

The write is ACCEPTED AND ECHOED BACK before the crash, so nothing at the
protocol layer signals a problem. `20` is in the published range `0..277` and is
the device's own roster entry for `Neural Amp Modeler 2`.

What makes ordinal 20 different was already visible in the schema, and is
recorded in `config/headrush_registry.json` once #122 lands (PR #128; this
document bases on `main` and does not depend on that file existing). Three
roster entries have an ordinal and no object behind them:

| ordinal | name | object | crash |
|---|---|---|---|
| 4 | ReValver Amp 2 | absent | NOT TESTED |
| 20 | Neural Amp Modeler 2 | absent | CONFIRMED |
| 254 | C-Verb 2 | absent | NOT TESTED |

@bschmalz81401 states the corresponding rule from the unit as one Capture and
one C-Verb per rig, so these are the second halves of blocks the hardware only
has one of.

**4 and 254 are deliberately untested.** The mechanism is clear, the cost of a
test is a crash and a power cycle, and the cost of refusing all three is
nothing. They are treated as unsafe on the same grounds, which is a judgement
and is labelled as one rather than reported as a measurement.

The NAM block itself is fine. After the first crash the unit rebooted into a rig
running ordinal `19`, `Neural Amp Modeler`, with no trouble at all. It is the
unbacked twin that is fatal, not the module.

### What this requires of #125

The adapter must REFUSE these ordinals at validation, before transport, in the
same class as the never-brick guard. An adapter that trusted the device's own
published roster would hand a planner a documented way to take the unit down,
and the device offers no signal that anything is wrong: the write succeeds, the
read-back agrees, and the engine dies afterwards. Read-back verification, which
#126 AC3 requires, does NOT catch this.

## FINDING 2: the API is gated behind HeadRush Remote

`/api/v1` is only mounted while HeadRush Remote is active on the unit. It did
not come back by itself after either crash.

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

## FINDING 3: continuous parameters are normalised, and the tapers are known

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

The last two rows are not linear, and the reason is that the curve is per
parameter. The device publishes an opaque id (`x-options.normalizeAlgo`) and no
formula. The formulas are in the vendor's own editor bundle, which the unit
serves, as a named enumeration:

```
0 Linear        4 TimeBiSquared   8  H3Volume
1 Db            5 Squared         9  AllenHeathFaderVolume
2 Volume        6 Exponential    10  H3ReverbTime
3 DelayRatio    7 MixerGain
```

`Amp.TremSpeed` carries id 5, `Squared`, which independently confirms the
quadratic measured off the screen. An absent id falls back to `Linear` in the
vendor's own code, which was previously a hypothesis this repo declined to act
on and is now the implementation.

Every one of the six readings above is reproduced exactly by the bundle's
formulas, with the formulas derived first and the readings used as the check.

PROVENANCE. This is the same class as `config/headrush_topologies.json`: read
out of the vendor's editor, not out of the API. Anything acting on it has to say
so. The DEVICE still does not publish the formula, and that remains true.

## Not verified, and why

| AC | status |
|---|---|
| 1. model and firmware | done, above |
| 2. discovery, rigs, topology, scenes, reads, verified writes | BLOCKED, verifies the adapter from #125 |
| 3. read-back after each write | partially exercised, and shown INSUFFICIENT by finding 1 |
| 4. non-allowlisted object-method refused before transport | BLOCKED, the allowlist is #125 |
| 5. record failures rather than weaken claims | done, findings 1 and 2 |
| 6. no destructive operations | honoured, see below |
| 7. scrubbed report | this document |
| 8. Prime and Flex Prime unverified | honoured, stated above |

Nothing was stored, reset, or flashed. Every write went to the edit buffer of
`##HRB ToneCommandTesting`, a rig created for this work, and the two crashes
confirmed the point by discarding everything: the unit came back with the rig as
it is on disk, empty.
