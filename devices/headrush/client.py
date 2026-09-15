"""Transport for a HeadRush device's web-editor API (#33 phase 1).

The unit self-describes over unauthenticated HTTP on port 80. There is nothing
to reverse engineer here, which is the whole reason #8 called this the easy
second device:

    GET  /api/v1/subtree{path}            schema and values below a path
    GET  /api/v1/object-meta{path}        schema for one object
    GET  /api/v1/object-properties{path}  current values for one object
    PUT  /api/v1/object-properties{path}  write values, {"Prop": value}
    POST /api/v1/object-method{path}/{m}  invoke, {"arguments": [...]}
    ws://{host}                           unsolicited change notifications

WHAT THIS MODULE IS NOT. It has no ToneCommand concepts in it: no
`DeviceAdapter`, no `Capabilities`, no effect ids, no slot numbers, no
`validate_action`. It is the layer `fm9/device.py` occupies for the FM9, and
like that layer it does not know a planner exists. A test pins this, because
the value of a transport that can be reviewed by someone with no HeadRush
depends on it staying free of everything else.

WRITES ARE IMMEDIATE AND UNGUARDED AT THIS LAYER. `set_properties` and
`call_method` change what the hardware is doing the moment they return.
Invariant 1 (nothing reaches hardware without an explicit human confirmation)
and Invariant 0 (`message_type_guard`, deny by default) live above this module
and are not implemented here. `call_method` in particular can invoke ANY method
the unit exposes, which is precisely the shape an allowlist exists for;
monzta1 and @bschmalz81401 agreed on #33 to decide that allowlist deliberately
at phase 4 rather than let it arrive by default. Until then, nothing in
ToneCommand calls this module.

PROVENANCE. Every behaviour below was measured on a HeadRush Core, firmware
5.1.0.2a63755, and is recorded on #109 and #33. The Python port was checked
against this machine's own resolver where the note says "measured"; where it
says otherwise, it is carried across from the TypeScript implementation at
github.com/bschmalz81401/HeadrushRigBuilder and labelled with what is and is
not known. Nothing here is inferred from a datasheet.
"""
from __future__ import annotations

import errno as _errno
import json
import os
import socket
import time
import urllib.error
import urllib.request
from typing import Any, Callable

__all__ = [
    "HeadrushClient",
    "NOTIFICATION_KINDS",
    "describe_unreachable",
    "device_origin",
    "parse_notification",
    "resolve_device_addresses",
]

DEFAULT_PORT = 80
DEFAULT_TIMEOUT_S = 30.0
DEFAULT_ATTEMPTS = 3
DEFAULT_RETRY_DELAY_S = 0.15

# A resolver answers with (address, family), family being 4 or 6. Injectable so
# the escalation ladder below can be tested without a network or a unit.
Resolver = Callable[[str, "int | None"], "list[tuple[str, int]]"]

# An opener performs one HTTP round trip and returns the raw body. Injectable
# for the same reason. It raises urllib's own exceptions so that callers, and
# describe_unreachable, see exactly what production sees.
Opener = Callable[[str, str, "bytes | None", "dict[str, str]", float], bytes]


# --- resolution ------------------------------------------------------------

# "A reply was dropped", not "there is no such name for long". mDNS is lossy by
# nature: one lookup in twelve on headrushcore.local threw with the unit up and
# answering curl the whole time, and that single throw used to reach the
# operator as "cannot reach the device" in front of a hardware write.
_LOSSY_LOOKUP_CODES = frozenset(
    c for c in (getattr(socket, "EAI_NONAME", None), getattr(socket, "EAI_AGAIN", None))
    if c is not None
)

# "This name has no address OF THE FAMILY I ASKED FOR", which is only ever a
# verdict on the filter, never on the name. Retried on attempt 0 alone, because
# that is the only attempt that carries a filter.
#
# Measured on this machine (macOS 15, CPython 3.12): a name nobody answers for,
# asked as AF_INET, raises EAI_NONAME after 5.005s, so macOS never reaches this
# set at all and is covered by _LOSSY_LOOKUP_CODES above. The set is for
# platforms that do distinguish the two, Linux being the one this shape is
# written for.
#
# WHAT THIS SET DOES NOT COVER, stated because an earlier draft of this comment
# claimed otherwise. The TypeScript port carries ENOENT here for Windows, where
# libuv maps WSANO_DATA (11004, "valid name, no data of the requested type")
# outside the EAI space (Grok, reviewing HeadrushRigBuilder#14, who read the
# libuv switch). ENOENT is errno 2 and is not an EAI code, so it would not match
# this set on any platform, and EAI_ADDRFAMILY is not defined on Windows at all.
# Whether CPython surfaces that distinction on Windows is unknown and untested,
# and it is left that way: adding ENOENT without a measurement would be the
# guess this project does not make. Cost of the set as it stands is at most one
# extra lookup on attempt 0, on platforms where the codes exist.
_FAMILY_MISS_CODES = frozenset(
    c for c in (getattr(socket, "EAI_ADDRFAMILY", None), getattr(socket, "EAI_NODATA", None))
    if c is not None
)


def _getaddrinfo_resolver(host: str, family: int | None) -> list[tuple[str, int]]:
    """Resolve through the OS, which is what does the mDNS work.

    Deliberately getaddrinfo and not a zeroconf library. The TypeScript
    implementation resolves through node:dns lookup, which is getaddrinfo, so
    the behaviour measured on #33 and #109 IS getaddrinfo behaviour, including
    the 5s .local cost. A zeroconf dependency would reimplement what
    mDNSResponder already does and would not reproduce what was measured.
    """
    af = socket.AF_UNSPEC if family is None else (
        socket.AF_INET if family == 4 else socket.AF_INET6
    )
    out: list[tuple[str, int]] = []
    seen: set[str] = set()
    for info in socket.getaddrinfo(host, None, af):
        # An IPv6 sockaddr is (address, port, flowinfo, scope_id) and the scope
        # is REAL here: measured on this machine, getaddrinfo("fe80::1%lo0")
        # answers ('fe80::1', 0, 0, 1). It is dropped on purpose rather than
        # unavailable, because a zone has no usable spelling in an http URL for
        # urllib, and _pick_address falls back to the hostname for exactly the
        # addresses where it would have mattered.
        address = info[4][0]
        if address in seen:
            continue
        seen.add(address)
        out.append((address, 4 if info[0] == socket.AF_INET else 6))
    return out


def resolve_device_addresses(
    host: str,
    *,
    resolve: Resolver | None = None,
    attempts: int = DEFAULT_ATTEMPTS,
    delay_s: float = DEFAULT_RETRY_DELAY_S,
    sleep: Callable[[float], None] = time.sleep,
) -> list[tuple[str, int]]:
    """Resolve the unit's name, tolerating a dropped mDNS reply.

    A fast name-not-found is retried, briefly and boundedly. Nothing else is: a
    lookup that times out is a name nobody is answering for, and paying that
    timeout three times only makes a unit that is off look slower.

    THE FIRST TRY ASKS FOR IPv4 ALONE, AND THAT IS A LATENCY FIX. A dual-family
    lookup waits for both answers, and on a network where the unit has no AAAA
    the resolver sits out its full timeout before returning the A record it
    already had. Measured on Brian's LAN, 2026-09-11:

        lookup(host, all)            5008 ms  ->  [10.8.72.116 (v4)] only
        lookup(host, family=4)          5 ms  ->  10.8.72.116

    Reproduced through this module against the same unit, CPython 3.12 on
    macOS, 2026-09-14, so this is a getaddrinfo property and not a Node one:

        getaddrinfo(host, AF_UNSPEC)   5002 ms  ->  [('10.8.72.116', 4)]
        getaddrinfo(host, AF_INET)        6 ms  ->  [('10.8.72.116', 4)]

    Both answers are identical. Roughly eight hundredfold, for nothing, and
    connect() prefers the v4 address whenever there is one. Every device call
    paid it.

    So the attempt budget doubles as an escalation ladder rather than growing a
    second loop: try IPv4, then the full lookup. A unit that answers on v4
    costs one fast query. A unit that answers only on IPv6 raises a retried
    code on the first try and the next try is the dual lookup that finds it. A
    unit that is off fails in the same number of tries it always did. That last
    part is unchanged, not improved, and deliberately so: a hardware write in
    front of an operator must not start hanging for longer than it used to.
    """
    resolver = resolve if resolve is not None else _getaddrinfo_resolver
    attempts = max(1, attempts)
    last: Exception | None = None
    for attempt in range(attempts):
        family = 4 if attempt == 0 else None
        try:
            records = resolver(host, family)
        except socket.gaierror as error:
            last = error
            code = error.errno
            retryable = code in _LOSSY_LOOKUP_CODES or (
                attempt == 0 and code in _FAMILY_MISS_CODES
            )
            if not retryable or attempt + 1 >= attempts:
                raise
            sleep(delay_s)
            continue
        # An empty answer is not an address, so escalate rather than hand
        # connect() nothing to use. This guards the injectable seam and a
        # future resolver; CPython's getaddrinfo raises for a real name rather
        # than returning [], so in production the gaierror path above is what
        # saves a v6-only unit.
        if not records and attempt + 1 < attempts:
            sleep(delay_s)
            continue
        return records
    if last is not None:
        raise last
    return []


def device_port() -> int:
    """The unit's HTTP port. Env override exists for a proxy, not for the unit."""
    raw = os.environ.get("HEADRUSH_PORT")
    if raw is None:
        return DEFAULT_PORT
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_PORT
    return value if value > 0 else DEFAULT_PORT


def request_timeout_s() -> float:
    raw = os.environ.get("HEADRUSH_TIMEOUT_MS")
    if raw is None:
        return DEFAULT_TIMEOUT_S
    try:
        value = float(raw) / 1000.0
    except ValueError:
        return DEFAULT_TIMEOUT_S
    return value if value > 0 else DEFAULT_TIMEOUT_S


def device_origin(address: str) -> str:
    """http://address, or http://address:port when the port is not 80."""
    port = device_port()
    return f"http://{address}" if port == DEFAULT_PORT else f"http://{address}:{port}"


# --- notifications ---------------------------------------------------------

NOTIFICATION_KINDS = frozenset({
    "propertyValueChanged",
    "objectAdded",
    "objectRemoved",
    "objectPropertyAdded",
    "objectPropertyRemoved",
    "webAccessChanged",
})


def parse_notification(data: str | bytes) -> dict[str, Any] | None:
    """Parse one WebSocket frame, or None if it is not a notification we know.

    Pure on purpose. The device pushes everything with no subscription
    handshake, so filtering is a client concern, and keeping the parse separate
    from the socket is what lets the whole notification vocabulary be tested
    with no unit on the network.
    """
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8")
        except UnicodeDecodeError:
            return None
    try:
        parsed = json.loads(data)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    kind = parsed.get("notification")
    if not isinstance(kind, str) or kind not in NOTIFICATION_KINDS:
        return None
    return parsed


# --- failure classification ------------------------------------------------

# From `errno`, NOT from `socket`. The socket module re-exports the EAI_* family
# but not these, so reading them off `socket` with a getattr default silently
# produced an EMPTY set and the errno branch below could never fire. Nothing in
# the fixture suite noticed, because a fixture raises ConnectionRefusedError or
# TimeoutError and those are caught by isinstance a line later. The unit found
# it: a request to an address with no route raises a bare OSError(EHOSTUNREACH)
# wrapped in URLError, and it was being reported as an unclassified failure
# rather than as a device that is not answering (2026-09-14, 10.8.72.199).
_NO_ANSWER_ERRNOS = frozenset({
    _errno.ECONNREFUSED,
    _errno.EHOSTUNREACH,
    _errno.ENETUNREACH,
    _errno.ETIMEDOUT,
    _errno.ECONNRESET,
})


def describe_unreachable(error: BaseException, target: str) -> str:
    """What to tell the operator when the unit could not be reached, by cause.

    Two different problems used to share one sentence. A name that did not
    resolve is usually mDNS dropping a reply: try again, and only then look at
    the network. A name that resolved to an address nothing answers on is a
    stale lease or a unit that is off, which is a different action.

    CLASSIFIED BY EXCEPTION TYPE AND ERRNO, NEVER BY MESSAGE TEXT. A message
    regex would also have caught the unit's own 504 Gateway Timeout on a
    wrong-argument method call, which is not "nothing answered" but the unit
    answering. urllib.error.HTTPError is therefore checked first, because the
    unit did reply. Every code but 404 is quoted back; 404 has two causes that
    want opposite actions and gets its own branch, below.

    Every branch ends a sentence, so a caller can append its own context after
    it with a plain space.
    """
    if isinstance(error, urllib.error.HTTPError):
        if error.code == 404:
            # A 404 has two causes that want opposite actions, and this
            # function cannot tell them apart without a second request, which
            # it has no business making. So it names both and says how to
            # check, rather than picking one and being confidently wrong half
            # the time.
            #
            # Measured on a Core, 2026-09-15 (#126): after the engine
            # crashed, `GET /` returned 200 from the editor's static files
            # while EVERY `/api/v1` path returned 404. That state was reported
            # as a plain 404, which reads as a wrong path and sends an operator
            # to check their own code.
            #
            # The editor's advice for it is to check HeadRush Remote, and that
            # is its GENERIC connection-failure message rather than a diagnosis
            # of this state: Remote was never toggled while the API was
            # watched, so the mechanism is a hypothesis. Hence "not serving its
            # API at all" as the observed fact and Remote as the thing to
            # check, rather than asserting the cause.
            return (
                f"The unit answered 404 for {target}. Either this firmware has "
                f"no such path, or the unit is not currently serving its API "
                f"at all: after a crash it was measured returning 404 for every "
                f"/api/v1 path while still serving its editor page, and the "
                f"editor's own advice for that state is to check HeadRush "
                f"Remote. Open http://<unit>/ in a browser to tell them apart: "
                f"if the page loads and reports it cannot connect, the problem "
                f"is the unit's API and not this path."
            )
        return _sentence(f"The unit answered {error.code} {error.reason} for {target}")

    cause: BaseException | None = error
    if isinstance(error, urllib.error.URLError):
        reason = error.reason
        cause = reason if isinstance(reason, BaseException) else None

    if isinstance(cause, socket.gaierror):
        return (
            f"The unit's name ({target}) did not resolve. It answers over mDNS, "
            f"which drops the odd query. Try again; if it keeps failing, check "
            f"the unit is on the same network as this machine."
        )
    if isinstance(cause, (TimeoutError, ConnectionRefusedError)) or (
        isinstance(cause, OSError) and cause.errno in _NO_ANSWER_ERRNOS
    ):
        return (
            f"Nothing answered at {target}. Check the unit is powered on and on "
            f"the network. A stale address lease looks exactly like this."
        )
    detail = str(getattr(error, "reason", "") or error) or type(error).__name__
    return _sentence(f"Cannot reach the device: {detail}")


def _sentence(text: str) -> str:
    return text if text.endswith((".", "!", "?")) else text + "."


# --- the client ------------------------------------------------------------

def _urllib_opener(
    url: str,
    method: str,
    body: bytes | None,
    headers: dict[str, str],
    timeout: float,
) -> bytes:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


class HeadrushClient:
    """A connected HeadRush unit, addressed once and reused.

    The address is resolved once because the OS does not cache mDNS: a .local
    name costs about 5s per request against about 130ms of real work.

    The unit publishes both an A and an AAAA record, and which one the resolver
    hands back varies BY PROCESS. The shell got IPv4 while the Next server got
    IPv6 link-local, from the same name at the same moment. Two things follow,
    and getting either wrong reads as a bare connection failure:

      - an IPv6 literal needs brackets in a URL
      - a link-local address is not usable in an http URL here

    THE SECOND POINT IS NARROWER IN PYTHON THAN IT WAS IN NODE, and the
    difference is worth stating because the two implementations look alike. In
    Node, `dns.lookup({all: true})` returns an address with no zone index at
    all, so a link-local answer genuinely cannot be turned into a URL. CPython
    is not in that position: `socket.getaddrinfo` returns the scope in the
    sockaddr, measured here as ('fe80::1', 0, 0, 1). What is true on both is
    that a zone has no spelling urllib will accept in an http URL, so the
    hostname is the only thing that works. The fallback is right; "the lookup
    does not return a zone" is a Node fact and is not repeated here as a
    CPython one.

    So prefer IPv4, take a routable v6 second, and otherwise fall back to the
    hostname and let the resolver deal with it per request. Slower, but it
    works, which the alternative does not. The address is not pinned for the
    session either: a leased A record can be stale while the hostname still
    answers, so a failed request retries once against the hostname and the
    address moves to the hostname for the rest of the session. It moves when
    the fallback is ATTEMPTED, not when it succeeds, because the evidence that
    the literal is dead is the failure that triggered the retry. Saying "a
    fallback that works" here would describe the narrower behaviour whose hole
    _request documents.
    """

    def __init__(
        self,
        host: str,
        address: str,
        *,
        opener: Opener | None = None,
        timeout_s: float | None = None,
    ):
        self.host = host
        self.address = address
        self._opener = opener if opener is not None else _urllib_opener
        self._timeout_s = timeout_s

    @classmethod
    def connect(
        cls,
        host: str,
        *,
        resolve: Resolver | None = None,
        attempts: int = DEFAULT_ATTEMPTS,
        delay_s: float = DEFAULT_RETRY_DELAY_S,
        sleep: Callable[[float], None] = time.sleep,
        opener: Opener | None = None,
        timeout_s: float | None = None,
    ) -> "HeadrushClient":
        records = resolve_device_addresses(
            host, resolve=resolve, attempts=attempts, delay_s=delay_s, sleep=sleep,
        )
        return cls(host, cls._pick_address(host, records),
                   opener=opener, timeout_s=timeout_s)

    @staticmethod
    def _pick_address(host: str, records: list[tuple[str, int]]) -> str:
        for address, family in records:
            if family == 4:
                return address
        for address, family in records:
            # Link-local is strictly fe80::/10, i.e. fe80: through febf:. This
            # tests the fe80: prefix alone, carried from the TypeScript port,
            # because SLAAC and every address this unit has produced is fe80::.
            # Widening it without an address that needs it would be a guess.
            if family == 6 and not address.lower().startswith("fe80:"):
                # Brackets, or every URL built from this is malformed.
                return f"[{address}]"
        # Link-local only, or nothing usable. The hostname is the only thing
        # left that can work. Note the reason precisely: _getaddrinfo_resolver
        # DISCARDED the scope a few lines up, and even holding it there is no
        # spelling for a zone that urllib accepts in an http URL. The scope was
        # available and dropped; it was not missing. Node's lookup is the one
        # that never offers it, and that fact does not belong here.
        return host

    @property
    def base_url(self) -> str:
        return f"{device_origin(self.address)}/api/v1"

    @property
    def ws_url(self) -> str:
        port = device_port()
        return f"ws://{self.address}" if port == DEFAULT_PORT else f"ws://{self.address}:{port}"

    def _fallback_base_url(self) -> str | None:
        if self.address == self.host:
            return None
        return f"{device_origin(self.host)}/api/v1"

    def _request_once(
        self, base: str, path: str, method: str = "GET", payload: Any = None,
    ) -> Any:
        headers: dict[str, str] = {}
        body: bytes | None = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        timeout = self._timeout_s if self._timeout_s is not None else request_timeout_s()
        raw = self._opener(f"{base}{path}", method, body, headers, timeout)
        # WRITES ANSWER 200 WITH AN EMPTY BODY. This cannot go straight to
        # json.loads: it raises on every single PUT, which is the failure that
        # cost the TypeScript implementation a debugging session before anyone
        # noticed the reply was simply empty rather than malformed.
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError as error:
            raise ValueError(f"{method} {path} returned non-JSON") from error

    def _request(self, path: str, method: str = "GET", payload: Any = None) -> Any:
        """One request, retried against the hostname only if nothing answered.

        THE RETRY IS FOR A STALE LEASE, NOT FOR A REPLY WE DID NOT LIKE. An
        HTTPError means the unit answered: a 404 for a path that does not
        exist, or the 504 it returns for a wrong-argument method call. Retrying
        those against the hostname cannot change the answer and doubles the
        cost of every one of them, which is measurable on a .local name.
        Observed against the unit on 2026-09-14: reading three container paths
        that legitimately 404 cost two round trips each for one verdict. The
        TypeScript implementation catches everything here and has the same
        defect; this is a deliberate divergence from it.
        """
        try:
            return self._request_once(self.base_url, path, method, payload)
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, OSError):
            fallback = self._fallback_base_url()
            if fallback is None:
                raise
            # AND THE FALLBACK STICKS, BEFORE the retry rather than after it.
            #
            # Retrying once satisfies the criterion, but leaving self.address on
            # an address we now have evidence is dead makes every later call in
            # the session pay that failure first. With the default 30 s timeout
            # and a unit whose lease moved, that is 30 s per request for an
            # answer already known to be somewhere else. The only test that
            # covered the retry used an instant ConnectionRefusedError, which is
            # why the cost was invisible.
            #
            # The assignment goes BEFORE the retry because the evidence that the
            # literal is dead is the failure just caught, not whatever the
            # hostname goes on to say. Assigning after a successful call looked
            # equivalent and was not: if the hostname answers 404 or 504 the
            # retry raises, the assignment never runs, and the next call pays the
            # dead literal again. That is the worst case to get wrong, because an
            # HTTPError is this module's own definition of the unit being alive,
            # so the composition proved the lease stale and then forgot it
            # (Grok, second review pass).
            #
            # WHAT STICKING DOES NOT BUY, measured against the unit with a dead
            # literal and a live name, 2026-09-14: 9.16 s, then 5.02 s, 5.03 s.
            # The dead address is paid exactly once, which is the fix. The
            # residual 5 s per call is urllib resolving the hostname itself on
            # every request with AF_UNSPEC, the same dual-family stall connect()
            # exists to avoid and which cannot be reached from here. Re-resolving
            # v4-first and pinning the new address would remove it; that is a
            # behaviour change with its own failure modes and is not smuggled
            # into a transport phase. Recorded rather than implied.
            #
            # This also moves ws_url onto the hostname for the rest of the
            # session, which is correct. No caller in this phase opens a socket.
            self.address = self.host
            return self._request_once(fallback, path, method, payload)

    # --- reads ---

    def subtree(self, path: str = "") -> Any:
        """Schema and values below path. The empty path is the whole tree."""
        return self._request(f"/subtree{path}")

    def object_meta(self, path: str) -> Any:
        """Schema for one object: property types, ranges in real units, methods."""
        return self._request(f"/object-meta{path}")

    def get_properties(self, path: str) -> Any:
        return self._request(f"/object-properties{path}")

    def get_property(self, path: str, name: str) -> Any:
        properties = self.get_properties(path)
        if not isinstance(properties, dict):
            return None
        return properties.get(name)

    # --- writes: immediate, unconfirmed, unguarded at this layer ---

    def set_properties(self, path: str, values: dict[str, Any]) -> Any:
        """Write values. Takes effect on the hardware immediately."""
        return self._request(f"/object-properties{path}", "PUT", values)

    def set_property(self, path: str, name: str, value: Any) -> Any:
        return self.set_properties(path, {name: value})

    def call_method(self, path: str, method: str, arguments: list[Any] | None = None) -> Any:
        """Invoke a method. Side effects depend entirely on the method.

        This endpoint can invoke ANY method the unit exposes. It is left
        unguarded here on purpose and is not reachable from ToneCommand; the
        allowlist is phase 4's deliberate decision, per #33.
        """
        body = self._request(
            f"/object-method{path}/{method}", "POST",
            {"arguments": list(arguments or [])},
        )
        if isinstance(body, dict):
            return body.get("methodReturnValue")
        return None
