"""One repo-wide em dash guard.

CLAUDE.md and AGENTS.md both say it plainly: no em dash anywhere, in code,
comments, docs or commit messages. Enforcement did not match. Seven separate
guards each carried a hardcoded list of "the files this phase touched", which
is a fine contract for a phase and a bad one for a repo rule: between them they
left `docs/UI-REDESIGN-SPEC.md` (17) and
`docs/CLAUDE-CODE-UI-IMPLEMENTATION-PROMPT.md` (5) unguarded from the start,
and nothing would have caught the next file either.

This walks every tracked text file instead, so a new file is covered by
existing, not by somebody remembering to extend a list. The per-phase guards
are left alone: they assert something narrower and they are not wrong.

WHY THE TWO EXCLUSIONS ARE NOT JUST "THE AWKWARD ONES"

Both are files this project must NOT edit, for reasons that have nothing to do
with punctuation, which is why they are named here with those reasons rather
than quietly skipped by a pattern.
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Path -> why this project may not rewrite it. Not "why the em dash is fine".
UNEDITABLE = {
    "config/fm9_catalog.json":
        "vendored verbatim from mcp-midi-control. AGENTS.md: refresh from "
        "upstream, never hand-edit. A punctuation change here would be a "
        "silent local fork of a file whose whole value is being unmodified.",
    "THIRD_PARTY_NOTICES.md":
        "the em dashes are inside a fenced block headed 'Reproduction of that "
        "project's NOTICE (as required by Apache-2.0)'. Apache-2.0 section 4 "
        "requires that NOTICE be reproduced, and reproducing it means "
        "reproducing it, punctuation included.",
}

#: Suffixes that are not text and will never be read as UTF-8.
BINARY = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".syx", ".woff",
          ".woff2", ".ttf", ".zip", ".wav", ".nam"}


def tracked_text_files():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    for rel in filter(None, out.split("\0")):
        if Path(rel).suffix.lower() in BINARY or rel in UNEDITABLE:
            continue
        yield rel


def test_no_em_dash_in_any_tracked_file():
    em_dash = chr(0x2014)          # spelled, so this file is not a hit itself
    offenders = []
    for rel in tracked_text_files():
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue               # binary without a known suffix, or a symlink
        if em_dash in text:
            offenders.append(f"{rel} ({text.count(em_dash)})")
    assert not offenders, (
        "em dash is banned repo-wide by CLAUDE.md; found in: "
        + ", ".join(sorted(offenders)))


def test_the_exclusions_still_exist_and_still_contain_one():
    """An exclusion that stops being true should fail loudly rather than sit
    here forever. If upstream drops the em dash, or the file is deleted, this
    list is stale and someone should find out from a test."""
    em_dash = chr(0x2014)
    for rel, reason in UNEDITABLE.items():
        path = ROOT / rel
        assert path.exists(), f"{rel} is excluded but no longer exists"
        assert em_dash in path.read_text(encoding="utf-8"), (
            f"{rel} no longer contains an em dash, so its exclusion is stale "
            f"and can be dropped. Recorded reason was: {reason}")
