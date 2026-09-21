# For any agent working in this repository

Read the knowledge base before touching code or the unit. It is local and
gitignored (`kb/`); if it is not here, stop and say so rather than guessing.

1. `kb/INDEX.md`, then `kb/index.json`: load `always_load` plus the files whose
   topics match your task. Do not load all of it.
2. Any Handsoff work (a lane, a review, a hardware pass): first the engine's
   own playbook, `handsoff playbook` (`lanes` before init, `landing` before
   advance 6, `lessons` once per session), then `kb/HANDSOFF.md` for what is
   ToneCommand's alone (topic `handsoff` in the index).
3. Anything that reaches the unit: `kb/HARDWARE_RULES.md` and the
   definition-of-done ladder in it.

Rules that hold even without the KB:

- Never widen a whitelist (`TONECOMMAND_STORE_SLOTS`, `TONECOMMAND_CAB_SLOTS`,
  `TONECOMMAND_NAM_SLOTS`) or touch the never-brick guard in `fm9/device.py`.
- No em dash anywhere: code, comments, docs, commit messages. No Co-Authored-By
  trailer on commits.
- Before every commit: `TONECOMMAND_SIM=1 .venv/bin/python -m pytest
  tests/test_device_handle.py tests/test_capability_gates.py
  tests/test_ui_warning.py -q` plus the suite for what you changed. A new route
  goes in tests/test_capability_gates.py's request table; a new
  `except Exception` goes in tests/data/broad_except_audit.json; ARCHITECTURE.md
  states server.py's measured line count; CHANGELOG.md gets the entry under
  Unreleased.
- A read after a write to the unit settles and retries; a test that passes on
  timing is not a pass.
- A deliverable that runs elsewhere (CI, another machine, the unit) is proven by
  that place's own run, recorded as evidence, or it is not done.
- Only one process holds the FM9's MIDI port; the server is stopped with
  SIGTERM, never SIGKILL.
