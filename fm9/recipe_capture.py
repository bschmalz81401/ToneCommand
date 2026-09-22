"""A recipe that uses a capture carries it by reference, never the file
(issue #153, I10).

A NAM capture is somebody's work under somebody's licence, and TONE3000
is where it lives. So a shared recipe names it: the TONE3000 tone and
model ids, the page url, the licence, the sha256 of the file the sharer
had, and the amp model the capture stands for on a device without it.
The recipient's ToneCommand then does one of four things, and says which:

    available    the sha256 is already in the recipient's library, or the
                 model downloaded under the recipient's OWN TONE3000 key
                 and hashed to the sha256 the recipe carries; the bytes go
                 to intake in memory, the same as a drop, and are not
                 written anywhere by this module or the server;
    not_yours    no key, 401, 403, or the tone is not public: the recipe
                 shows the TONE3000 link and fetches nothing;
    missing      404, or the download hashed to something else (TONE3000
                 has a different file under that id now);
    unreachable  a timeout, a connection failure, a 5xx, a malformed
                 answer or a url that is not TONE3000's.

In every case but the first the recipe builds with `stands_for`, and the
line names the amp the capture stood for. Redistribution never happens
through us: this module has no store, no cache and no upload.

Confirmed live against api/v1 on 2026-09-20: a tone carries `is_public`
and `license` and no price field; `/models/<id>` is the model record and
its `model_url` (`/api/v1/models/<id>/download/<file>.nam`) is the file;
`/download` without the file name is a 404; both need the caller's own
key (401 without one). Paid or private material is therefore what TONE3000
refuses to the recipient's key, never a price this code cannot see.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
from pathlib import Path
from typing import Any, Callable

SOURCE = "TONE3000"
HOST = "www.tone3000.com"
URL_PREFIX = "https://www.tone3000.com/"
MAX_BYTES = 8 * 1024 * 1024
MAX_FIELD_BYTES = 1024
MAX_TONE_ID = 10_000_000
MAX_MODEL_ID = 100_000_000
KEYS = frozenset({"source", "tone_id", "model_id", "url", "license", "sha256", "stands_for"})
STANDS_FOR_KEYS = frozenset({"block", "type_name"})
STATUSES = ("available", "not_yours", "missing", "unreachable")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
_ENV_KEY = "TONE3000_SECRET_KEY"

#: What a smuggled file looks like inside a JSON field: a data URI, a
#: base64 run long enough to be a model, or bytes that are not text.
_DATA_URI = re.compile(r"^\s*data:", re.I)
_B64_RUN = re.compile(r"^[A-Za-z0-9+/=\s]{512,}$")


class RecipeCaptureError(ValueError):
    """One line, written for the person reading the recipe."""


# --- the sharer's side ----------------------------------------------------------------

def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def serialise(nam_bytes: bytes, tone_id: int, model_id: int, url: str,
              license: str, stands_for: str) -> dict:
    """The capture field for a recipe: ids, url, licence, the hash of the
    file the sharer has, and the amp it stands for. Never the bytes."""
    if not isinstance(nam_bytes, (bytes, bytearray)) or not nam_bytes:
        raise RecipeCaptureError("no capture file to hash")
    field = {"source": SOURCE, "tone_id": int(tone_id), "model_id": int(model_id),
             "url": str(url), "license": str(license or "")[:32],
             "sha256": sha256_of(bytes(nam_bytes)),
             "stands_for": {"block": "amp", "type_name": str(stands_for)}}
    why = validate_field(field)
    if why:
        raise RecipeCaptureError(why)
    return field


def _smuggled(value: Any) -> bool:
    if isinstance(value, (bytes, bytearray)):
        return True
    if isinstance(value, str):
        if _DATA_URI.match(value):
            return True
        if len(value) >= 512 and _B64_RUN.match(value):
            try:
                base64.b64decode(value, validate=True)
                return True
            except Exception:
                return False
    if isinstance(value, dict):
        return any(_smuggled(v) for v in value.values())
    if isinstance(value, list):
        return any(_smuggled(v) for v in value)
    return False


def validate_field(cap: Any, amp_types: set[str] | None = None) -> str | None:
    """One line naming what is wrong with a capture field, or None. Closed
    keys, one source, integer ids in range, a TONE3000 url, a 64-hex
    sha256, a stands_for on the amp roster when the roster is given, and
    nothing that could be the file itself."""
    if not isinstance(cap, dict):
        return "the recipe's capture is not a record"
    if _smuggled(cap):
        return "the recipe's capture carries file data; a capture is shared by reference only"
    extra = set(cap) - KEYS
    if extra:
        return f"the recipe's capture has a field this format does not have: {sorted(extra)[0]}"
    missing = KEYS - set(cap)
    if missing:
        return f"the recipe's capture is missing {sorted(missing)[0]}"
    if cap.get("source") != SOURCE:
        return f"the recipe's capture source is {cap.get('source')!r}; only {SOURCE} is understood"
    for key, top in (("tone_id", MAX_TONE_ID), ("model_id", MAX_MODEL_ID)):
        v = cap.get(key)
        if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= top:
            return f"the recipe's capture {key} is not a TONE3000 id"
    url = cap.get("url")
    if not isinstance(url, str) or not url.startswith(URL_PREFIX):
        return "the recipe's capture url is not a TONE3000 page"
    if not isinstance(cap.get("license"), str) or len(cap["license"]) > 32:
        return "the recipe's capture licence is not a short label"
    if not isinstance(cap.get("sha256"), str) or not _SHA.match(cap["sha256"]):
        return "the recipe's capture sha256 is not 64 hex characters"
    sf = cap.get("stands_for")
    if not isinstance(sf, dict) or set(sf) != STANDS_FOR_KEYS:
        return "the recipe's capture stands_for must name a block and a type_name"
    if sf.get("block") != "amp":
        return "the recipe's capture stands_for block must be the amp"
    if not isinstance(sf.get("type_name"), str) or not sf["type_name"].strip():
        return "the recipe's capture stands_for has no type_name"
    if amp_types is not None and sf["type_name"] not in amp_types:
        return f"the recipe's capture stands for {sf['type_name']!r}, which is not an amp model here"
    if len(repr(cap).encode()) > MAX_FIELD_BYTES:
        return "the recipe's capture field is too large"
    return None


def validate(recipe: Any, amp_types: set[str] | None = None) -> str | None:
    """None for a recipe without a capture field, or with a valid one."""
    if not isinstance(recipe, dict) or "capture" not in recipe:
        return None
    return validate_field(recipe.get("capture"), amp_types)


# --- the recipient's side -------------------------------------------------------------

def key_from_env() -> str | None:
    """The recipient's own TONE3000 credential: the OAuth access token from
    the app's sign-in (#88, refreshed when it is about to expire) first,
    else the secret key from env or .env, else None."""
    from fm9 import tone3000_auth
    store = tone3000_auth.TokenStore()
    if store.load():
        # Signed in: the token is the credential, full stop. An expired
        # token whose refresh failed means "sign in again", never a fall
        # back to the secret key, which would fetch under a different
        # entitlement than the one the player agreed to (review F1.1).
        return tone3000_auth.access_token(store)
    key = os.environ.get(_ENV_KEY, "").strip()
    if not key and _ENV_FILE.exists():
        for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(_ENV_KEY + "="):
                key = line.split("=", 1)[1].strip()
                break
    return key or None


def replacement_url(cap: dict) -> str:
    """The app's route that starts TONE3000's load_tone flow for this
    capture: TONE3000 verifies the account's access and, when the tone is
    unavailable to it, lets the player browse a replacement under their
    own entitlement (#88)."""
    return f"/api/tone3000/load?tone_id={int(cap['tone_id'])}"


def default_fetch(url: str, key: str | None, timeout: float = 20.0) -> tuple[int, bytes]:
    """GET a TONE3000 model download under the caller's key. (status,
    body); the body is capped at MAX_BYTES and never written to disk.
    Only www.tone3000.com is ever contacted; anything else is (0, b'')."""
    import urllib.error
    import urllib.request
    if not url.startswith(URL_PREFIX):
        return 0, b""
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {key}" if key else "",
        "User-Agent": "ToneCommand (recipe capture by reference)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return int(r.status), r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        return int(e.code), b""
    except (urllib.error.URLError, OSError, ValueError):
        return 0, b""


def _model_url(body: bytes) -> str | None:
    """The download url out of a model record, or None when the record is
    not one or points off TONE3000."""
    import json
    try:
        doc = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    doc = doc.get("data", doc) if isinstance(doc, dict) else None
    url = doc.get("model_url") if isinstance(doc, dict) else None
    if not isinstance(url, str) or not url.startswith(URL_PREFIX + "api/v1/models/"):
        return None
    return url


def resolve(cap: dict, library: set[str] | None = None,
            fetch: Callable[[str, str | None], tuple[int, bytes]] | None = None,
            key: str | None = None, is_public: bool | None = None) -> dict:
    """One of the four outcomes, with the line and the link. `library` is
    the recipient's sha256 set (the UI's known list). `fetch(url, key)`
    returns (status, bytes); the default contacts TONE3000. The bytes,
    when they arrive and match, are returned in memory for intake and
    nothing here keeps them."""
    why = validate_field(cap)
    if why:
        raise RecipeCaptureError(why)
    stood = cap["stands_for"]["type_name"]
    link = cap["url"]
    base = {"sha256": cap["sha256"], "stood_for": stood, "link": link, "bytes": None,
            "replacement_url": replacement_url(cap)}

    def out(status: str, line: str, data: bytes | None = None) -> dict:
        assert status in STATUSES
        return {**base, "status": status, "line": line, "bytes": data}

    if cap["sha256"] in (library or set()):
        return out("available", "the capture this recipe uses is in your library")
    if is_public is False:
        return out("not_yours", f"the capture this recipe uses is not public on {SOURCE}; "
                                f"built with {stood} instead: {link}")
    if not key:
        return out("not_yours", f"the capture this recipe uses is on {SOURCE}; sign in to "
                                f"{SOURCE} in Settings to fetch it, built with {stood} for now: {link}")
    fetch = fetch or default_fetch
    # Two calls, both under the recipient's key: the model record (its
    # model_url carries the file name the download route needs; confirmed
    # live, /download without it is a 404), then the file itself.
    try:
        status, body = fetch(f"{URL_PREFIX}api/v1/models/{int(cap['model_id'])}", key)
    except Exception:
        status, body = 0, b""
    if status == 200:
        model_url = _model_url(body)
        if model_url is None:
            return out("unreachable", f"{SOURCE} answered something this version cannot read "
                                      f"for this capture; built with {stood} for now: {link}")
        try:
            status, data = fetch(model_url, key)
        except Exception:
            status, data = 0, b""
    else:
        data = b""
    if status in (401, 403):
        return out("not_yours", f"{SOURCE} did not allow your account to fetch this capture; "
                                f"built with {stood} instead: {link}")
    if status == 404:
        return out("missing", f"the capture this recipe uses is gone from {SOURCE}; "
                              f"built with {stood} instead: {link}")
    if status != 200 or not data or len(data) > MAX_BYTES:
        return out("unreachable", f"{SOURCE} did not answer for this capture; built with "
                                  f"{stood} for now: {link}")
    if sha256_of(data) != cap["sha256"]:
        return out("missing", f"{SOURCE} has a different file under this capture's id now; "
                              f"built with {stood} instead: {link}")
    return out("available", "the capture this recipe uses is fetched under your own "
                            f"{SOURCE} account and matches the recipe's hash", data)
