#!/usr/bin/env python3
"""One-command definition-of-done gate for a preset range.

    python tools/preset_doctor.py 139 154
    TONECOMMAND_SIM=1 python tools/preset_doctor.py 0 1

Runs, in order:
1. path_audit    - every named scene has a live Input-to-Output signal
                   path (the check that catches silent scenes).
2. audit_scenes  - scene names vs engaged effects (owner-convention
                   flags only if kb/conventions.json exists).
3. level_report  - level facts per scene, plus owner-convention flags
                   when configured.

Exit 0 only if all three pass. This is the mandatory pre-"done" gate
from the project's verification ladder; ears remain the final judge of
whether a passing preset sounds good.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import path_audit, audit_scenes, level_report  # noqa: E402


def main(a: int, b: int) -> int:
    results = {}
    for name, tool in (("path_audit", path_audit),
                       ("audit_scenes", audit_scenes),
                       ("level_report", level_report)):
        print(f"\n=== {name} {a}-{b} ===")
        results[name] = tool.main(a, b)
    print("\n=== doctor verdict ===")
    bad = [k for k, rc in results.items() if rc != 0]
    for k, rc in results.items():
        print(f"{k}: {'PASS' if rc == 0 else 'FAIL'}")
    print("ears: PENDING (always)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]), int(sys.argv[2])))
