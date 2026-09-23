# HeadRush display conversion, verified against a Core

Run 2026-09-20 against a HeadRush Core on firmware `5.1.0.2a63755`, on a
`##HRB` test preset, with `tools/verify_headrush_display.py`.

## What this closes, and what it does not

Every automated test for the display path runs against `HeadrushSim` and
**replays** numbers recorded during the #130 and #167 sessions. That is real
evidence about the curve table and the arithmetic. It was never evidence that

```python
HeadrushAdapter(client, registry, tapers=tapers.load())
```

works against firmware, because nothing had constructed that combination
against a unit: `tools/verify_headrush.py` builds the adapter with **no**
table, so its AC5 exercises the refusal and never the conversion.

**What the screen rows are, precisely.** The values were read from the web
editor the unit serves, not from the Core's front panel, and the owner
attests the two agree. That matters, because `devices/headrush/tapers.py` was
built by extracting the vendor's own curve functions **from that editor's
bundle**. So the six rows below confirm a chain (we reproduce the editor, the
editor matches the unit) rather than being an independent reading of the
hardware. #130's `hardware_check` rows say "taken off a Core's screen"; these
are not those, and are recorded as what they are.

The 990-point vector test in #130 already establishes the first link far more
thoroughly than six comparisons can. The rows that carry new information are
the three below the table.

## Transcript

```
display conversion against <host>, on a ##HRB preset

[PASS] Amp.Bass at wire 0.75
       the adapter says the screen reads '75 %' [Linear]; read '75 %'
[PASS] Amp.Treble at wire 0.5
       the adapter says the screen reads '50 %' [Linear]; read '50 %'
[PASS] Amp.PostGain at wire 0.5
       the adapter says the screen reads '0.0 dB' [Linear]; read '0.0 dB'
[PASS] Amp.TremDepth at wire 0.0
       the adapter says the screen reads '0 %' [Linear]; read '0 %'
[PASS] Amp.TremSpeed at wire 0.5
       the adapter says the screen reads '5.19 Hz' [Squared]; read '5.19 Hz'
[PASS] Amp.TremSpeed at wire 0.25   (write reported ok=False)
       the adapter says the screen reads '1.48 Hz' [Squared]; read '1.48 Hz'
[PASS] Amp.TremSpeed: set_param_display(5.19)
       the unit is showing '5.19 Hz' (grid 0.01); write reported ok=True
[PASS] an adapter built with no table still refuses
       Amp.Bass: the wire value cannot be shown as a display value

8 passed, 0 failed, 0 skipped
```

Every value written was restored; the rig was reloaded afterwards to discard
the edit buffer.

## Three findings

### 1. #167 reproduced live, through this path

`Amp.TremSpeed` at wire `0.25` reported **`ok=False` while displaying the
correct `1.48 Hz`**. Every previous observation of #167 was through the raw
wire API; this is the first through `set_param_wire` under the display
methods. The conversion is right, the displayed text is right, the flag is
wrong, which is exactly what the adapter's docstring and the PR claim.

### 2. #167 is narrower than it was stated

`set_param_display(5.19)` reported **`ok=True`**, where this was predicted to
fail. `5.19` is already **on** the 0.01 grid, so the unit had nothing to snap
and stored what it was sent.

So the rule is not "a quantized parameter always reports failure". It is that
**a request that falls between grid points reports failure**, because that is
when the unit's snap moves the value. Worth recording against #167, which
reads as the broader claim.

### 3. No write can restore a quantized parameter exactly

Restoring `Amp.TremSpeed` to the `0.5` it held wrote back `0.5001265406608582`,
the same float #167 measured. There is **no wire value a caller can send** that
returns the stored float to `0.5`, because the write is converted, snapped and
converted back on the way in. It displays identically at `5.19 Hz`.

This is the device working as designed, and the first version of this script
reported it as a failed restore telling the operator to reload the rig. It now
compares what the unit *shows* and says so.

## Two defects this run found in the script itself

- **It prompted with API property names.** `Amp.PostGain` is **"Output
  Level"** on screen, and nothing in `config/headrush_registry.json` maps the
  two: the schema publishes no label. The operator was sent to the "High
  Volume" control, and a mis-read control would have been recorded as a failed
  conversion rather than as a mis-read. `SCREEN_LABELS` now carries the unit's
  own wording for the parameters this script prompts for; a parameter absent
  from it is prompted by property name rather than by a guess.
- **The restore check was too strict.** See finding 3.

Both were found by running it against the unit, which is the argument for
running a verification script before committing it rather than after.


---

# #167 fixed, verified on the same Core (2026-09-20)

Re-run after `fix/write-verify-predicts-the-snap` (#173), same unit, same
`##HRB` preset, same six readings. **8 passed, 0 failed.**

## The symptom is gone

```
BEFORE (#168 tip)
[PASS] Amp.TremSpeed at wire 0.25
       wire 0.25 written (ok=False); the adapter says the screen reads '1.48 Hz'

AFTER (#173)
[PASS] Amp.TremSpeed at wire 0.25
       wire 0.25 written (ok=True);  the adapter says the screen reads '1.48 Hz'
```

That is the whole of #167 on a real device: a write the unit honoured, which
the adapter called a failure, now reported as the success it always was. The
displayed value is unchanged, because the conversion was never the problem.

## What did NOT change, which is the part that could have broken

`set_param_display(5.19)` still reports **`ok=True`**. That request lands ON
the 0.01 grid, so the unit stores it exactly and an exact check is
achievable. A tolerance window applied uniformly would have stopped requiring
that, and this run is the evidence the prediction did not.

## A second-order confirmation nobody asked for

The previous run ended with a warning: restoring `Amp.TremSpeed` to the `0.5`
it held wrote back `0.5001265406608582`, because no write returns a quantized
parameter to an off-grid float.

This run restored **all five parameters with no note at all**. The reason is
the model itself: every value the device *holds* is already the image of a
grid point, so writing one back is a fixed point and reads back identical.
The restore only ever looked broken because the run before it had started
from a value the unit could not have been holding.

## Transcript

```
[PASS] Amp.Bass at wire 0.75            '75 %'    read '75 %'
[PASS] Amp.Treble at wire 0.5           '50 %'    read '50%'
[PASS] Amp.PostGain at wire 0.5         '0.0 dB'  read '0.0 dB'
[PASS] Amp.TremDepth at wire 0.0        '0 %'     read '0 %'
[PASS] Amp.TremSpeed at wire 0.5        '5.19 Hz' read '5.19 Hz'   (ok=True)
[PASS] Amp.TremSpeed at wire 0.25       '1.48 Hz' read '1.48 Hz'   (ok=True)
[PASS] Amp.TremSpeed: set_param_display(5.19)     ok=True
[PASS] an adapter built with no table still refuses

restored 5 parameter(s) to the value they held before this run
8 passed, 0 failed, 0 skipped
```

Screen values read from the web editor, which the owner attests matches the
device, the same provenance as the run above, and the same caveat.
