#!/usr/bin/env python3
"""Verify Pedal 2 drives the wah: poll WAH_CONTROL while the pedal rocks."""
import time

from fm9.device import FM9
from fm9.registry import Registry

reg = Registry()
fm9 = FM9(reg)
fm9.status_dump()
spec = reg.spec("WAH", 5)   # WAH_CONTROL
samples = []
print("polling WAH_CONTROL for 10 seconds; rock Pedal 2 now...")
t0 = time.time()
while time.time() - t0 < 10:
    v = fm9.get_param_display(spec)
    if isinstance(v, (int, float)):
        samples.append(v)
        print(f"  {time.time() - t0:4.1f}s  control={v}")
    time.sleep(0.7)
spread = (max(samples) - min(samples)) if samples else 0
print(f"\nsamples: {len(samples)}, min={min(samples) if samples else '?'}, "
      f"max={max(samples) if samples else '?'}, spread={spread:.2f}")
print("PEDAL LINK CONFIRMED" if spread > 1.0 else
      "no movement detected: pedal not linked or not rocked")
fm9.close()
