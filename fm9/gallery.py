"""The Gift of Tone gallery's device gate and its click-time fetch
(issues #156 J3 and #157 J4), on top of the J1 catalog (fm9/gift_of_tone).

Three rules this module owns:

- Only what this unit can take is offered. Every catalog entry carries the
  device matrix Fractal's page states; an entry with no version for the
  connected device is not shown rather than shown and failing, and one
  whose minimum firmware is newer than the unit's answers a single line.
- Nothing from Fractal is mirrored. The zip is fetched from the catalog's
  own fractalaudio.com URL, by the player's app, at click time, and the
  bytes are checked against the catalog's sha256 BEFORE any of it is
  opened. A mismatch installs nothing; so does a fetch that fails.
- Only what the catalog listed is handed on. Members are matched to the
  entry's contents; anything unexpected is named and skipped.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import socket
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

#: The adapter's device kind to the name the catalog's device matrix uses.
CATALOG_DEVICE = {"fm9": "FM9"}
ALL_DEVICES = "All devices"

#: The only host the app fetches Gift of Tone files from (kb/SITE.md,
#: "Fractal sources: terms and fetch policy").
SOURCE_PREFIX = "https://www.fractalaudio.com/"

#: Zips can carry tens of MB of extras; refuse anything absurd.
MAX_DOWNLOAD = 64 * 1024 * 1024

NO_DEVICE_LINE = ("no device connected: browse away, installing needs the "
                  "unit plugged in")

BLOCKS_NOTE = "effect blocks (.blk) are not installable from here yet"


class GalleryError(ValueError):
    """One line, written for the person who clicked."""


# --- J3: device and firmware ---------------------------------------------

def device_name(kind: str | None) -> str | None:
    return CATALOG_DEVICE.get(kind or "")


def _for_device(entry: dict, name: str) -> dict | None:
    """The matrix row for `name`, an 'All devices' row, or None."""
    rows = entry.get("devices") or []
    for row in rows:
        if row.get("device") == name:
            return row
    for row in rows:
        if row.get("device") == ALL_DEVICES:
            return row
    return None


def entries_for(entries: list[dict], kind: str | None) -> list[dict]:
    """The entries with a version for this device kind. With no device
    (None) the whole catalog: browsing needs no unit."""
    name = device_name(kind)
    if name is None:
        return list(entries)
    return [e for e in entries if _for_device(e, name) is not None]


def parse_version(label: str | None) -> tuple[int, ...] | None:
    """'12.00' -> (12, 0); '4.0' -> (4, 0); '' or nonsense -> None."""
    if not label:
        return None
    parts = re.findall(r"\d+", str(label))
    return tuple(int(x) for x in parts[:3]) if parts else None


def min_firmware(entry: dict, kind: str | None) -> str | None:
    row = _for_device(entry, device_name(kind) or "")
    return (row or {}).get("min_firmware") or None


def firmware_gate(entry: dict, kind: str | None,
                  firmware_label: str | None) -> str | None:
    """None when the unit may take this entry; otherwise the one line.

    Compares the firmware as the unit writes it (device.firmware_label,
    '12.00') with the entry's minimum for this device. A label the unit
    did not give, or one that does not parse, refuses too: the gate
    cannot say yes to what it cannot compare, and nothing is fetched or
    installed after a refusal.
    """
    name = device_name(kind)
    if name is None:
        return NO_DEVICE_LINE
    row = _for_device(entry, name)
    if row is None:
        return f"this pack has no {name} version"
    need = parse_version(row.get("min_firmware"))
    if need is None:
        return None                     # the page states no minimum
    have = parse_version(firmware_label)
    if have is None:
        return (f"could not read this unit's firmware (it answered "
                f"{firmware_label!r}); this pack needs {name} firmware "
                f"{row['min_firmware']} or newer, so nothing is installed "
                "until the unit answers")
    if have < need:
        return (f"this pack needs {name} firmware {row['min_firmware']} or "
                f"newer; this unit runs {firmware_label}")
    return None


# --- J4: fetch, verify, cache, unpack -------------------------------------

def cache_dir() -> Path:
    override = os.environ.get("TONECOMMAND_CACHE_DIR", "").strip()
    base = Path(override) if override else Path.home() / ".tonecommand" / "cache"
    return base / "gift-of-tone"


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ToneCommand"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD:
        raise GalleryError("that download is larger than anything a preset "
                           "bundle should be; refusing it")
    return data


def fetch_entry(entry: dict, fetch=None, cache: Path | None = None
                ) -> tuple[bytes, str]:
    """(bytes, 'cached' | 'fetched') for a catalog entry.

    The URL must be Fractal's own. A cached copy is re-hashed before it is
    trusted. A fresh download is hashed before anything else happens to
    it; only a match is cached. Every failure is one line.
    """
    url = str(entry.get("url") or "")
    want = str(entry.get("sha256") or "").lower()
    if not url.startswith(SOURCE_PREFIX):
        raise GalleryError("that entry's file is not on fractalaudio.com; "
                           "the app only fetches from the source")
    if len(want) != 64:
        raise GalleryError("that entry carries no sha256; nothing is "
                           "fetched without one")
    cache = cache if cache is not None else cache_dir()
    path = cache / f"{want}.zip"
    if path.exists():
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() == want:
            return data, "cached"
        path.unlink(missing_ok=True)          # a damaged cache file
    fetch = fetch or _download
    try:
        data = fetch(url)
    except GalleryError:
        raise
    except urllib.error.HTTPError as e:
        raise GalleryError(f"the source answered {e.code} for that file; "
                           "nothing was installed") from e
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as e:
        why = getattr(e, "reason", None) or e
        raise GalleryError(f"could not reach fractalaudio.com ({why}); "
                           "nothing was installed") from e
    got = hashlib.sha256(data).hexdigest()
    if got != want:
        raise GalleryError("the file at the source has changed since it was "
                           "catalogued (its sha256 no longer matches); "
                           "nothing was unpacked or installed")
    try:
        cache.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)
    except OSError:
        pass                        # a cache is a courtesy, never the point
    return data, "fetched"


def expected_members(entry: dict) -> dict[str, str]:
    """zip member path -> kind for everything the catalog says is in the
    zip, exactly as the catalog spells it (the catalog was built from the
    zips, so presets at the root are bare names and bundles carry their
    folder). A bundle's own `members` are files INSIDE the .fasBundle,
    read only by bundlefile through the bundle's map, never matched here."""
    c = entry.get("contents") or {}
    out: dict[str, str] = {}
    for kind in ("presets", "cabs", "blocks", "other"):
        for name in c.get(kind) or []:
            out[str(name)] = kind
    for b in c.get("bundles") or []:
        out[str(b.get("file", ""))] = "bundles"
    out.pop("", None)
    return out


def unpack(entry: dict, data: bytes) -> tuple[list[dict], list[str]]:
    """(members, unexpected). Members are {name, path, kind, raw} for the
    files the catalog listed, matched by their exact path in the zip;
    `unexpected` names every other file, which is never handed on, so a
    stray file that borrows a listed name in another folder is refused
    too. Directory entries and AppleDouble junk are ignored."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise GalleryError(f"the download is not a readable zip: {e}") from e
    want = expected_members(entry)
    members, unexpected, seen = [], [], set()
    for info in zf.infolist():
        if info.is_dir():
            continue
        path = info.filename
        base = path.rsplit("/", 1)[-1]
        if base.startswith("._") or base == ".DS_Store":
            continue
        kind = want.get(path)
        if kind is None or path in seen:
            unexpected.append(path)
            continue
        seen.add(path)
        members.append({"name": base, "path": path, "kind": kind,
                        "raw": zf.read(info)})
    return members, unexpected


def find_entry(entries: list[dict], entry_id: str) -> dict | None:
    for e in entries:
        if e.get("id") == entry_id:
            return e
    return None


def find_by_artist(entries: list[dict], query_words: list[str]) -> dict | None:
    """The newest entry whose artists contain every word, or None. Same
    rule as acquire.find, over the catalog's `artists` list."""
    if not query_words:
        return None
    best = None
    for e in entries:
        hay = " ".join(e.get("artists") or []).lower()
        if all(w in hay for w in query_words):
            if best is None or (e.get("year") or 0) > (best.get("year") or 0):
                best = e
    return best
