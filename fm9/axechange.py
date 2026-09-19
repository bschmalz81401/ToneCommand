"""One Axe-Change link, one preset (issue #160, J7).

The player pastes a detail page URL. This reads THAT page's details block
(the label/value pairs the page shows a human), refuses a page it cannot
read in one line, checks the preset's product and firmware against the
connected unit with J3's wording, fetches download.php for that id from
axechange.fractalaudio.com only, validates it as a preset file, and hands
it to the guarded install. No crawling, no search, no catalog of
Axe-Change: one link the player brought.
"""
from __future__ import annotations

import html as _html
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import gallery, presetfile

HOST = "axechange.fractalaudio.com"
MAX_DOWNLOAD = 8 * 1024 * 1024

UNREADABLE = "could not read that page"

#: What the page calls the product, to the catalog's device names (which
#: gallery.device_name maps adapter kinds onto).
PRODUCT_NAMES = {"fm9": "FM9", "axe-fx iii": "Axe-Fx III", "fm3": "FM3"}


class AxeChangeError(ValueError):
    """One line, written for the person who pasted the link."""


@dataclass
class Detail:
    id: int
    name: str
    author: str
    product: str
    firmware: str
    setup: str
    description: str
    band: str = ""
    song: str = ""
    genre: str = ""
    instrument: str = ""
    downloads: str = ""
    uploaded: str = ""

    @property
    def line(self) -> str:
        bits = [f"{self.name} by {self.author}", f"for {self.product}",
                f"firmware {self.firmware}", self.setup]
        head = ", ".join(b for b in bits if b)
        return head + (f": {self.description}" if self.description else "")

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "author": self.author,
                "product": self.product, "firmware": self.firmware,
                "setup": self.setup, "description": self.description,
                "band": self.band, "song": self.song, "genre": self.genre,
                "instrument": self.instrument, "downloads": self.downloads,
                "uploaded": self.uploaded, "line": self.line}


def parse_url(url: str) -> int:
    """The preset id in an Axe-Change detail link, or AxeChangeError."""
    try:
        u = urllib.parse.urlparse(str(url or "").strip())
    except ValueError as e:
        raise AxeChangeError("that is not an Axe-Change link") from e
    if u.scheme not in ("http", "https") or u.netloc.lower() != HOST:
        raise AxeChangeError("that is not an Axe-Change link")
    if not u.path.endswith("detail.php"):
        raise AxeChangeError("that is not an Axe-Change preset page "
                             "(detail.php?preset=<id>)")
    q = urllib.parse.parse_qs(u.query)
    pid = (q.get("preset") or [""])[0]
    if not pid.isdigit():
        raise AxeChangeError("that Axe-Change link names no preset id")
    return int(pid)


def is_axechange_link(text: str) -> bool:
    try:
        parse_url(text)
        return True
    except AxeChangeError:
        return False


_DETAILS = re.compile(r'<div\s+class="details"\s*>(.*?)</div>', re.S)
_PAIR = re.compile(r'<p class="group"[^>]*>\s*<span>(.*?)</span>\s*<span>(.*?)</span>\s*</p>', re.S)


def parse_page(page: str) -> Detail:
    """The details block's label/value pairs. A page without the block, or
    without a Name and a Fractal Product, is one line: could not read it."""
    m = _DETAILS.search(page or "")
    if not m:
        raise AxeChangeError(UNREADABLE)
    fields = {}
    for label, value in _PAIR.findall(m.group(1)):
        fields[_html.unescape(re.sub(r"<[^>]+>", "", label)).strip()] = \
            _html.unescape(re.sub(r"<[^>]+>", "", value)).strip()
    if not fields.get("Name") or not fields.get("Fractal Product"):
        raise AxeChangeError(UNREADABLE)
    try:
        pid = int(fields.get("ID") or "0")
    except ValueError:
        pid = 0
    return Detail(id=pid, name=fields["Name"], author=fields.get("Author", ""),
                  product=fields["Fractal Product"],
                  firmware=fields.get("Firmware Version", ""),
                  setup=fields.get("Set Up", ""),
                  description=fields.get("Description", ""),
                  band=fields.get("Band/Artist/Player", ""),
                  song=fields.get("Song", ""), genre=fields.get("Genre", ""),
                  instrument=fields.get("Instrument", ""),
                  downloads=fields.get("Downloads", ""),
                  uploaded=fields.get("Date Uploaded", ""))


def gate(detail: Detail, kind: str | None, firmware_label: str | None) -> str | None:
    """None when the connected unit can take this preset; else one line in
    J3's wording. The page says '8.x', so majors are compared."""
    if kind is None:
        return gallery.NO_DEVICE_LINE
    name = gallery.device_name(kind)
    want = PRODUCT_NAMES.get(detail.product.strip().lower(), detail.product.strip())
    if name is None or want.lower() != name.lower():
        return (f"this preset is for the {detail.product}, not the {name or kind}; "
                "presets are device-specific and it was not installed")
    need = gallery.parse_version(detail.firmware)
    if need is None:
        return None
    have = gallery.parse_version(firmware_label)
    if have is None:
        return (f"could not read this unit's firmware (it answered "
                f"{firmware_label!r}); this preset needs {name} firmware "
                f"{detail.firmware} or newer, so nothing is installed until "
                "the unit answers")
    if have[:1] < need[:1]:
        return (f"this preset needs {name} firmware {detail.firmware} or newer; "
                f"this unit runs {firmware_label}")
    return None


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) ToneCommand",
        "Accept": "text/html,*/*"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD:
        raise AxeChangeError("that download is larger than any preset file; "
                             "refusing it")
    return data


def _wrap(fetch, url: str, what: str) -> bytes:
    try:
        return fetch(url)
    except AxeChangeError:
        raise
    except urllib.error.HTTPError as e:
        raise AxeChangeError(f"Axe-Change answered {e.code} for {what}; "
                             "nothing was installed") from e
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as e:
        why = getattr(e, "reason", None) or e
        raise AxeChangeError(f"could not reach Axe-Change ({why}); nothing "
                             "was installed") from e


def fetch_page(pid: int, fetch=None) -> str:
    data = _wrap(fetch or _get, f"https://{HOST}/detail.php?preset={pid}", "that page")
    return data.decode("utf-8", "replace")


def cache_dir() -> Path:
    override = os.environ.get("TONECOMMAND_CACHE_DIR", "").strip()
    base = Path(override) if override else Path.home() / ".tonecommand" / "cache"
    return base / "axechange"


def fetch_preset(pid: int, fetch=None, cache: Path | None = None) -> bytes:
    """download.php for that id, from the one host, validated as a preset
    file before it is cached or handed on."""
    cache = cache if cache is not None else cache_dir()
    path = cache / f"{pid}.syx"
    if path.exists():
        data = path.read_bytes()
        try:
            presetfile.parse(data)
            return data
        except presetfile.PresetFileError:
            path.unlink(missing_ok=True)
    data = _wrap(fetch or _get, f"https://{HOST}/download.php?preset={pid}",
                 "that preset")
    try:
        presetfile.parse(data)
    except presetfile.PresetFileError as e:
        raise AxeChangeError(f"the download for preset {pid} is not a preset "
                             f"file ({e}); nothing was installed") from e
    try:
        cache.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)
    except OSError:
        pass
    return data
