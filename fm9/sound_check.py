"""Per-scene captures for the balance rules (issue #102 G3).

`capture_scenes` switches the unit through the scenes asked for, records
the test capture on each (capture.record, the G1 method), and comes back
to the scene it found loaded, whatever happens in between. It touches
nothing else: the caller has already put the routing in the re-amp state
under routing.temporary, and that context manager puts it back. Proven on
the simulator with a fake recorder; the live proof is the next rig
session's, and the issues say so.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import capture


class SoundCheckError(RuntimeError):
    """One line, written for the person at the rig."""


def capture_scenes(fm9: Any, scenes: list[int], recorder: Callable, directory: Path,
                   routing: Any = None) -> dict:
    """{origin_scene, captures: [{scene, name, wav, path}], returned: bool}.
    `recorder(signal, seconds, out_channel)` is reamp.replay_and_record on
    hardware and a fake in tests. Stops at the first scene that fails and
    still returns to the origin scene."""
    want = [int(s) for s in scenes]
    if any(not 1 <= s <= 8 for s in want):
        raise SoundCheckError("scenes are 1 to 8")
    current = fm9.scene_name()
    origin = current[0] if current else None
    if origin is None:
        raise SoundCheckError("the unit did not say which scene is loaded; nowhere to come back to")
    preset = fm9.current_preset()
    out: list[dict] = []
    returned = False
    try:
        for s in want:
            if s != fm9.scene_name()[0]:
                fm9.set_scene(s)
            name = fm9.scene_name(s)
            side = capture.record("test", directory, recorder=recorder, preset=preset,
                                  scene=s, routing=routing)
            out.append({"scene": s, "name": name[1] if name else None,
                        "wav": side["wav"], "path": side["path"],
                        "rms_dbfs": side["rms_dbfs"], "peak_dbfs": side["peak_dbfs"]})
    finally:
        now = fm9.scene_name()
        if now and now[0] != origin:
            fm9.set_scene(origin)
        after = fm9.scene_name()
        returned = bool(after and after[0] == origin)
    return {"origin_scene": origin, "captures": out, "returned": returned}
