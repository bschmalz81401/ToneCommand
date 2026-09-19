"""The Gift of Tone catalog, read the way recipes are read (issue #154, J1).

catalog/gift_of_tone.json is committed in the repository and published by the
site at /gift-of-tone.json. The app reads it site-first, then from the
repository's contents API, then from the local checkout, so a player never
needs a GitHub account and an offline machine still sees the catalog it
shipped with. The catalog holds URLs, hashes and bundle maps only; the zips
are fetched at click time from fractalaudio.com and verified against the
pinned sha256 (kb/SITE.md, "Fractal sources: terms and fetch policy").
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = os.environ.get("TONECOMMAND_RECIPE_REPO", "monzta1/ToneCommand")
BRANCH = os.environ.get("TONECOMMAND_RECIPE_BRANCH", "main")
SITE = os.environ.get("TONECOMMAND_SITE", "https://tonecommand.com").rstrip("/")
LOCAL = Path(__file__).resolve().parent.parent / "catalog" / "gift_of_tone.json"
_TTL = 600
_cache: dict = {"doc": None, "at": 0.0, "source": None}


def _get_json(url: str, timeout: float, accept: str = "application/json"):
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "ToneCommand"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _valid(doc) -> bool:
    return isinstance(doc, dict) and isinstance(doc.get("entries"), list) and all(
        isinstance(e, dict) and e.get("url", "").startswith("https://www.fractalaudio.com/") and len(e.get("sha256", "")) == 64
        for e in doc["entries"])


def _from_site(timeout: float) -> dict:
    doc = _get_json(f"{SITE}/gift-of-tone.json", timeout)
    if not _valid(doc):
        raise ValueError("the site's catalog is not the expected shape")
    return doc


def _from_github(timeout: float) -> dict:
    listing = _get_json(f"https://api.github.com/repos/{REPO}/contents/catalog/gift_of_tone.json?ref={BRANCH}",
                        timeout, accept="application/vnd.github+json")
    doc = _get_json(listing["download_url"], timeout)
    if not _valid(doc):
        raise ValueError("the repository's catalog is not the expected shape")
    return doc


def _from_local() -> dict:
    doc = json.loads(LOCAL.read_text(encoding="utf-8"))
    if not _valid(doc):
        raise ValueError("the local catalog is not the expected shape")
    return doc


def fetch(timeout: float = 6.0) -> tuple[dict | None, str, str | None]:
    """(catalog, source, why_not): site first, repository second, the local
    file last. A failure names every source that did not answer."""
    now = time.time()
    if _cache["doc"] is not None and now - _cache["at"] < _TTL:
        return _cache["doc"], _cache["source"], None
    errors = []
    sources = ([("site", lambda: _from_site(timeout))] if SITE else []) + \
        [("github", lambda: _from_github(timeout)), ("local", _from_local)]
    for label, get in sources:
        try:
            doc = get()
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
            errors.append(f"{label}: {e}")
            continue
        _cache.update(doc=doc, at=now, source=label)
        return doc, label, None
    return None, "none", "could not read the Gift of Tone catalog (" + "; ".join(errors) + ")"


def entries(timeout: float = 6.0) -> list[dict]:
    doc, _, _ = fetch(timeout)
    return list(doc["entries"]) if doc else []
