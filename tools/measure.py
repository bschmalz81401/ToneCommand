#!/usr/bin/env python3
"""The measurement ears on a capture, from the terminal (#101 #102 #103).

    tools/measure.py <capture.wav>                     one capture
    tools/measure.py <capture.wav> --request "big wide clean"
    tools/measure.py <capture.wav> --baseline <other.wav>
    tools/measure.py <captures folder>                 every wav, one line each
    tools/measure.py --balance 1:clean:<a.wav> 2:rhythm:<b.wav> 3:lead:<c.wav>

Reads files; writes nothing; touches no device.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fm9 import measure  # noqa: E402


def _line(m: measure.Measurement, name: str) -> str:
    if not m.valid:
        return f"{name}: INVALID, {m.invalid_reason}"
    lo = (m.loudness or {}).get("integrated_lufs")
    st = m.stereo or {}
    bands = ", ".join(f"{k} {v:+.1f}" for k, v in m.bands.items())
    return (f"{name}: {m.status}; {lo if lo is not None else 'n/a'} LUFS; bands dB vs whole: {bands}; "
            f"corr {st.get('correlation', 'n/a')}{' (mono-like)' if st.get('mono_like') else ''}; "
            f"crest {m.dynamics['crest_db']:.1f} dB" + (f"; findings: " + " | ".join(f.line for f in m.findings)
                                                        if m.findings else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("target", nargs="?", help="a wav or a folder of wavs")
    ap.add_argument("--request", default="", help="the player's words, for the width finding")
    ap.add_argument("--baseline", default="", help="a wav to compare bands against")
    ap.add_argument("--reference", default="", help="a reference clip: the band deltas and the amp moves (G6)")
    ap.add_argument("--balance", nargs="*", metavar="SCENE:ROLE:WAV", help="scene balance across captures")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.balance:
        rows = []
        for spec in a.balance:
            scene, role, wav = spec.split(":", 2)
            m = measure.measure(wav)
            rows.append({"scene": int(scene), "role": role,
                         "lufs": (m.loudness or {}).get("integrated_lufs") if m.valid else None})
        out = measure.scene_balance(rows)
        print(json.dumps(out, indent=1) if a.json else
              f"{out['status']}; baseline {out.get('baseline')}; " +
              (" | ".join(f["line"] for f in out["findings"]) or "no balance finding") +
              (f"; missing: {', '.join(out['missing_facts'])}" if out.get("missing_facts") else ""))
        return 0
    if a.reference:
        if not a.target:
            ap.error("--reference needs the build capture as the target")
        from fm9 import tone_match
        mt = tone_match.match(a.target, a.reference, Path(a.reference).stem)
        if a.json:
            print(json.dumps(mt, indent=1))
        else:
            for ln in mt["lines"]:
                print(ln)
            for m in mt["moves"]:
                print(f"   move: {m['why']} (one step of {m['step']:g}; re-measure to verify)")
            if not mt["moves"]:
                print("   within 1.5 dB in every band: nothing to move")
        return 0
    if not a.target:
        ap.error("give a wav, a folder, or --balance")
    p = Path(a.target)
    paths = sorted(p.glob("*.wav")) if p.is_dir() else [p]
    for wav in paths:
        m = measure.measure(wav, request=a.request or None)
        if a.json:
            d = m.as_dict()
            if a.baseline:
                d["compare"] = measure.compare(m, measure.measure(a.baseline), Path(a.baseline).stem)
            print(json.dumps(d, indent=1))
        else:
            print(_line(m, wav.name))
            if a.baseline and m.valid:
                for ln in measure.compare(m, measure.measure(a.baseline), Path(a.baseline).stem)["lines"]:
                    print("   " + ln)
    return 0


if __name__ == "__main__":
    sys.exit(main())
