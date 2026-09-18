"""Issue #6 (narrowed remaining scope): retrieval over the full cab catalog.

curated_cab_roster() only ever surfaces a deduped slice of bank 3 (issue
#45), so banks 0 and 1 - most of the ~2,235-entry factory catalog - are
invisible to the planner no matter what the player asks for. These tests
prove the two new pieces: a search that reaches the whole catalog, and a
per-request grounding note that surfaces a real match without inlining the
whole catalog or bypassing validation/confirm.
"""
from __future__ import annotations

import server
from fm9.registry import Registry


def _first_non_bank3_entry():
    """A real bank-0/1 catalog entry whose name has a word long enough (>=6
    chars) to be picked up by cab_retrieval_context's own matching floor."""
    reg = Registry()
    for bank_key, roster in reg.cab_rosters.items():
        if str(bank_key) == "3" or not roster:
            continue
        for ordn, name in roster.items():
            if any(len(w) >= 6 for w in str(name).split()):
                return int(bank_key), int(ordn), str(name)
    raise AssertionError("no non-bank-3 cab roster entry with a distinctive word found")


def _distinctive_word(name: str) -> str:
    return max(name.split(), key=len)


def test_full_catalog_search_finds_entries_outside_curated_subset():
    bank, ordn, name = _first_non_bank3_entry()
    curated = {(b, o) for b, o, _ in server.curated_cab_roster()}
    assert (bank, ordn) not in curated, (
        "test fixture picked an entry curated_cab_roster() already carries; "
        "pick a genuinely bank-0/1-only entry instead")

    # A distinctive substring of the real name must find it via full search.
    needle = _distinctive_word(name)
    results = server.full_cab_catalog_search(needle, limit=50)
    assert (bank, ordn, name) in results


def test_full_catalog_search_is_empty_for_blank_or_unmatched_query():
    assert server.full_cab_catalog_search("") == []
    assert server.full_cab_catalog_search("zzznonexistentcabnamezzz") == []


def test_matching_cab_mentions_are_appended_to_planner_context():
    bank, ordn, name = _first_non_bank3_entry()
    needle = _distinctive_word(name)
    ctx = server.cab_retrieval_context(f"build me a rig with the {needle} cabinet")
    assert f"bank {bank} cab {ordn}" in ctx
    assert name in ctx


def test_no_match_produces_no_context_noise():
    # Ordinary tone-descriptor vocabulary must never false-match a cab name
    # that merely happens to contain one of these words as a substring.
    ctx = server.cab_retrieval_context("build me a warm clean tone")
    assert ctx == ""


def test_a_curated_cab_already_visible_is_not_repeated():
    # A request naming a cab curated_cab_roster() ALREADY lists must not be
    # duplicated in the retrieval note - that would be noise, not new
    # grounding.
    curated = next((c for c in server.curated_cab_roster()
                    if any(len(w) >= 6 for w in c[2].split())), None)
    assert curated, "fixture needs a curated entry with a distinctive word"
    _, _, name = curated
    needle = _distinctive_word(name)
    ctx = server.cab_retrieval_context(f"use the {needle} cab")
    assert name not in ctx


def test_retrieval_never_selects_or_sends_anything():
    """The note is text for the planner to read, never a write. Neither
    function may itself reach the device or perform a write - they may
    still MENTION set_cab in the instructional text they generate, the same
    way curated_cab_roster()/param_reference() already do."""
    import inspect
    for forbidden in ("fm9.", "get_fm9", "_fm9", ".apply(", "run_action"):
        assert forbidden not in inspect.getsource(server.cab_retrieval_context)
        assert forbidden not in inspect.getsource(server.full_cab_catalog_search)
