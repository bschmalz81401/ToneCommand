"""Advisory mode, the deterministic half (#68 compare, #69 close the gap,
#70 diagnose).

Everything here reads edit-buffer captures (fm9.editbuffer.capture) and
returns findings. It never produces an action and imports nothing that could
run one: the whole point of Epic A is that the tool can talk about a tone
without touching the rig, and the #72 guarantee (advice carries no actions)
is a property of this module's shape, not of a prompt. The model, when it is
in the loop at all, writes prose ON TOP of these numbers; it is never asked
to measure anything itself.

Three things it knows how to say:

  compare(a, b)    what differs between two captures, exhaustively: blocks
                   present on one side only, engagement, active channel,
                   type identity (the amp model, the cab, any typed family),
                   and every parameter value on each capture's active channel
  gap(diffs)       the concrete moves that take A toward B, as advice, plus
                   one sentence the planner could act on if the player asks
  diagnose(cap)    for a named symptom, a curated table of checks against the
                   capture, each answering likely / cleared / not readable
                   with the value it saw and the rule it rests on
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

# --- the little the module needs to know about the FM9 ----------------------

#: Which parameter carries a family's TYPE (its model), and the registry
#: roster that names the ordinal. Mirrors server.TYPE_PARAMS on purpose:
#: this module never imports the server.
TYPE_PARAMS = {"DISTORT": (10, "amp_roster"), "FUZZ": (0, "drive_roster"),
               "REVERB": (10, "reverb_roster")}
CAB_BANK_PID, CAB_TYPE_PID = 0, 4

#: Amp knobs by parameter id on the DISTORT block (registry labels).
AMP = {"gain": 11, "bass": 12, "mid": 13, "treble": 14, "master": 15,
       "low_cut": 16, "high_cut": 17, "depth": 26, "presence": 30}
MIX_PID = {"DELAY": 0, "REVERB": 0, "CHORUS": 10, "FUZZ": 4}
TIME_PID = {"DELAY": 12}

#: Words in a cab's name that say something about its voice, for the one
#: judgement compare makes about cabs. Name keywords, not measurement:
#: IRCommand's measured axis is the follow-on.
BRIGHT_WORDS = ("v30", "57", "sm57", "bright", "cap", "2x12", "1x12", "jensen",
                "greenback", "g12h")
DARK_WORDS = ("4x12", "ribbon", "r121", "121", "160", "dark", "cone", "room",
              "t75", "k100", "4x10")


@dataclass
class Difference:
    kind: str            # presence | engaged | channel | type | cab | param
    block: str           # family
    instance: int
    label: str           # what differs, for a human
    a: object = None
    b: object = None
    param_id: int | None = None
    channel: int | None = None
    raw: bool = False    # compared on the wire, no display value

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Advice:
    block: str
    instance: int
    param: str
    frm: object
    to: object
    why: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Cause:
    check: str
    verdict: str         # likely | cleared | not readable
    value: object
    rule: str
    wording: str

    def as_dict(self) -> dict:
        return asdict(self)


# --- reading a capture --------------------------------------------------------

def _stride(values: list, channels: int) -> int:
    return len(values) // channels if channels > 1 else len(values)


def _wire(block: dict, pid: int, channel: int | None = None):
    """The raw wire value of one parameter on the block's active channel
    (or the given one), or None when the capture does not carry it."""
    vals = block.get("values") or []
    if not vals:
        return None
    ch = block.get("channel", 0) if channel is None else channel
    stride = _stride(vals, max(1, block.get("channels", 1)))
    i = ch * stride + pid
    return vals[i] if 0 <= i < len(vals) else None


def _display(reg, block: dict, pid: int, wire):
    """(display value or None, label). Uncalibrated parameters give None."""
    try:
        spec = reg.spec(block["family"], pid, block["instance"])
    except Exception:
        spec = None
    label = (spec.label if spec is not None and spec.label else f"param {pid}")
    if wire is None or spec is None or spec.dmin is None:
        return None, label
    from fm9.protocol import normalized_to_display
    return round(normalized_to_display(wire / 65534, spec.dmin, spec.dmax,
                                       spec.scale), 2), label


def value(reg, cap: dict, family: str, pid: int, instance: int = 1):
    """One display value from a capture, or None if the block or the
    parameter is not there. The diagnose table reads through this."""
    for b in cap.get("blocks") or []:
        if b["family"] == family and b["instance"] == instance:
            return _display(reg, b, pid, _wire(b, pid))[0]
    return None


def _block(cap: dict, family: str, instance: int = 1):
    for b in cap.get("blocks") or []:
        if b["family"] == family and b["instance"] == instance:
            return b
    return None


def type_name(reg, block: dict) -> str | None:
    """The model this block is set to, named through the registry, or None
    for a family with no typed roster."""
    fam = block["family"]
    if fam == "CABINET":
        bank = _wire(block, CAB_BANK_PID)
        ordn = _wire(block, CAB_TYPE_PID)
        if bank is None or ordn is None:
            return None
        try:
            return reg.cab_description(int(ordn), int(bank))
        except Exception:
            return f"cab {ordn} (bank {bank})"
    pid, roster_attr = TYPE_PARAMS.get(fam, (None, None))
    if pid is None:
        return None
    ordn = _wire(block, pid)
    if ordn is None:
        return None
    roster = getattr(reg, roster_attr, {}) or {}
    return str(roster.get(str(int(ordn)), roster.get(int(ordn), f"type {ordn}")))


def cab_voice(name: str | None) -> str | None:
    """'bright' / 'dark' / None from a cab's name words."""
    n = (name or "").lower()
    bright = any(w in n for w in BRIGHT_WORDS)
    dark = any(w in n for w in DARK_WORDS)
    if bright and not dark:
        return "bright"
    if dark and not bright:
        return "dark"
    return None


# --- #68 compare ----------------------------------------------------------------

def compare(a: dict, b: dict, reg) -> list[Difference]:
    """Everything that differs between two captures. Walks BOTH sides by
    (family, instance): a block on one side only is a Difference in its own
    right rather than a silent omission; values are compared on each
    capture's ACTIVE channel; a parameter with no display value is compared
    on the wire and flagged raw; identity (amp model, cab, typed families)
    is a Difference of its own, named through the registry."""
    out: list[Difference] = []
    key = lambda blk: (blk["family"], blk["instance"])
    ak = {key(blk): blk for blk in a.get("blocks") or []}
    bk = {key(blk): blk for blk in b.get("blocks") or []}
    for k in sorted(set(ak) | set(bk)):
        fam, inst = k
        x, y = ak.get(k), bk.get(k)
        if x is None or y is None:
            side = "B" if x is None else "A"
            present = y if x is None else x
            out.append(Difference("presence", fam, inst,
                                  f"{fam} {inst} only in {side}"
                                  + (" (engaged)" if not present.get("bypassed") else " (bypassed)"),
                                  a=x is not None, b=y is not None))
            continue
        if bool(x.get("bypassed")) != bool(y.get("bypassed")):
            out.append(Difference("engaged", fam, inst, f"{fam} {inst} engaged",
                                  a=not x.get("bypassed"), b=not y.get("bypassed")))
        if x.get("channel") != y.get("channel"):
            out.append(Difference("channel", fam, inst, f"{fam} {inst} channel",
                                  a="ABCD"[x.get("channel", 0)], b="ABCD"[y.get("channel", 0)]))
        tx, ty = type_name(reg, x), type_name(reg, y)
        if tx != ty and (tx is not None or ty is not None):
            out.append(Difference("cab" if fam == "CABINET" else "type", fam, inst,
                                  "cab" if fam == "CABINET" else f"{fam} model", a=tx, b=ty))
        # parameters, each on its own capture's active channel
        xv, yv = x.get("values") or [], y.get("values") or []
        if not xv or not yv:
            continue
        sx, sy = _stride(xv, max(1, x.get("channels", 1))), _stride(yv, max(1, y.get("channels", 1)))
        skip = {TYPE_PARAMS.get(fam, (None,))[0], CAB_BANK_PID if fam == "CABINET" else None,
                CAB_TYPE_PID if fam == "CABINET" else None}
        for pid in range(min(sx, sy)):
            if pid in skip:
                continue
            wx, wy = _wire(x, pid), _wire(y, pid)
            if wx == wy:
                continue
            dx, label = _display(reg, x, pid, wx)
            dy, _ = _display(reg, y, pid, wy)
            if dx is None and dy is None:
                out.append(Difference("param", fam, inst, label, a=wx, b=wy, param_id=pid,
                                      channel=x.get("channel"), raw=True))
            else:
                out.append(Difference("param", fam, inst, label, a=dx, b=dy, param_id=pid,
                                      channel=x.get("channel")))
    return out


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def narrate(diffs: list[Difference], a_label: str = "A", b_label: str = "B") -> list[str]:
    """Plain-language lines, one thing each, B described relative to A."""
    if not diffs:
        return [f"{a_label} and {b_label} are the same: no difference."]
    lines: list[str] = []
    for d in diffs:
        fam = d.block.title() if d.block != "DISTORT" else "amp"
        if d.kind == "presence":
            lines.append(f"{d.label.replace('only in A', 'is only in ' + a_label).replace('only in B', 'is only in ' + b_label)}.")
        elif d.kind == "engaged":
            lines.append(f"{b_label} has {fam} {d.instance} {'on' if d.b else 'off'}; {a_label} has it {'on' if d.a else 'off'}.")
        elif d.kind == "channel":
            lines.append(f"{fam} {d.instance} sits on channel {d.b} in {b_label} and {d.a} in {a_label}.")
        elif d.kind == "type":
            lines.append(f"{b_label} uses the {d.b} {fam.lower()}; {a_label} uses the {d.a}.")
        elif d.kind == "cab":
            va, vb = cab_voice(d.a), cab_voice(d.b)
            voice = (f", a {vb}er cab" if vb and va and vb != va else
                     f", a {vb} cab" if vb and not va else "")
            lines.append(f"{b_label} runs the {d.b} cab{voice}; {a_label} runs the {d.a}.")
        elif d.kind == "param" and d.raw:
            lines.append(f"{fam} {d.instance} {d.label} differs (raw {d.a} vs {d.b}).")
        else:
            try:
                more = float(d.b) > float(d.a)
            except (TypeError, ValueError):
                more = None
            word = {"Time": ("longer", "shorter"), "Mix": ("wetter", "drier"),
                    "Gain": ("more", "less"), "Feedback": ("more", "less")}.get(
                d.label.split()[0], ("higher", "lower"))
            if more is None:
                lines.append(f"{fam} {d.instance} {d.label}: {b_label} {_fmt(d.b)}, {a_label} {_fmt(d.a)}.")
            else:
                lines.append(f"{b_label} has {word[0] if more else word[1]} {fam.lower()} "
                             f"{d.label.lower()} ({_fmt(d.b)} vs {_fmt(d.a)}).")
    return lines


# --- #69 close the gap -------------------------------------------------------------

def gap(diffs: list[Difference], reg=None) -> tuple[list[Advice], str]:
    """The moves that take A toward B, as advice, and one sentence the
    planner could act on. No action objects exist here and none are built:
    build_prompt is text a player may choose to send to /api/plan."""
    advice: list[Advice] = []
    parts: list[str] = []
    for d in diffs:
        fam = d.block
        if d.kind == "engaged":
            advice.append(Advice(fam, d.instance, "bypass", "on" if d.a else "off",
                                 "on" if d.b else "off",
                                 f"the target has {fam} {d.instance} {'on' if d.b else 'off'}"))
            parts.append(f"turn {fam.lower()} {d.instance} {'on' if d.b else 'off'}")
        elif d.kind == "type":
            advice.append(Advice(fam, d.instance, "model", d.a, d.b, "the target uses a different model"))
            parts.append(f"set {fam.lower()} {d.instance} to {d.b}")
        elif d.kind == "cab":
            advice.append(Advice(fam, d.instance, "cab", d.a, d.b, "the target runs a different cab"))
            parts.append(f"select cab {d.b}")
        elif d.kind == "channel":
            advice.append(Advice(fam, d.instance, "channel", d.a, d.b, "the target sits on another channel"))
            parts.append(f"put {fam.lower()} {d.instance} on channel {d.b}")
        elif d.kind == "param" and not d.raw:
            advice.append(Advice(fam, d.instance, d.label, d.a, d.b,
                                 f"the target sets {d.label.lower()} to {_fmt(d.b)}"))
            parts.append(f"set {fam.lower()} {d.instance} {d.label.lower()} to {_fmt(d.b)}")
        elif d.kind == "presence" and d.b and not d.a:
            advice.append(Advice(fam, d.instance, "block", "absent", "present",
                                 "the target has this block and you do not"))
            parts.append(f"add {fam.lower()} {d.instance}")
    return advice, ("; ".join(parts) if parts else "")


# --- #70 diagnose ---------------------------------------------------------------------

@dataclass
class Check:
    id: str
    read: object           # callable(reg, cap) -> value or None
    predicate: object      # callable(value) -> bool
    rule: str              # 'rule N' present in config/tone_rules.md
    wording: str           # what a likely verdict says
    unit: str = ""


def _amp(pid):
    return lambda reg, cap: value(reg, cap, "DISTORT", pid)


def _mix(fam):
    def read(reg, cap):
        blk = _block(cap, fam)
        if blk is None or blk.get("bypassed"):
            return 0.0 if blk is not None else None
        return value(reg, cap, fam, MIX_PID[fam])
    return read


def _engaged(fam):
    def read(reg, cap):
        blk = _block(cap, fam)
        return None if blk is None else (not blk.get("bypassed"))
    return read


def _cab_voice_read(reg, cap):
    blk = _block(cap, "CABINET")
    return cab_voice(type_name(reg, blk)) if blk is not None else None


def _eq_engaged(reg, cap):
    blks = [b for b in cap.get("blocks") or [] if b["family"] in ("GEQ", "PEQ")]
    return any(not b.get("bypassed") for b in blks) if blks else False


SYMPTOMS: dict[str, tuple[str, list[Check]]] = {
    "muddy": ("low-mid build-up and too much wet", [
        Check("amp_bass_high", _amp(AMP["bass"]), lambda v: v >= 7, "rule 9",
              "amp bass is high; muddy rhythm is a rule 9 failure, pull bass back toward 5"),
        Check("amp_low_cut_low", _amp(AMP["low_cut"]), lambda v: v < 60, "rule 9",
              "the amp's low cut is below 60 Hz; the low end is uncontrolled", "Hz"),
        Check("reverb_wet", _mix("REVERB"), lambda v: v >= 40, "rule 11",
              "reverb mix is high on a rhythm; wet is the minority of the mix"),
        Check("delay_wet", _mix("DELAY"), lambda v: v >= 40, "rule 11",
              "delay mix is high; the dry attack is being swallowed"),
        Check("cab_dark", _cab_voice_read, lambda v: v == "dark", "rule 9",
              "the cab name reads dark; a darker cab thickens the low mids"),
    ]),
    "boomy": ("uncontrolled low end", [
        Check("amp_bass_high", _amp(AMP["bass"]), lambda v: v >= 7, "rule 9",
              "amp bass is high"),
        Check("amp_depth_high", _amp(AMP["depth"]), lambda v: v >= 7, "rule 9",
              "amp depth is high; depth adds low end below the cab"),
        Check("amp_low_cut_low", _amp(AMP["low_cut"]), lambda v: v < 60, "rule 9",
              "the amp's low cut is below 60 Hz", "Hz"),
    ]),
    "thin": ("no body: mids scooped, no drive in front, a small cab", [
        Check("amp_mid_low", _amp(AMP["mid"]), lambda v: v <= 3, "rule 19",
              "amp mid is scooped; rule 19 keeps the low-mid body"),
        Check("amp_bass_low", _amp(AMP["bass"]), lambda v: v <= 3, "rule 9",
              "amp bass is low"),
        Check("no_boost", _engaged("FUZZ"), lambda v: v is False, "rule 3",
              "no drive block is engaged in front of the amp"),
        Check("cab_bright", _cab_voice_read, lambda v: v == "bright", "rule 9",
              "the cab name reads bright and small"),
    ]),
    "harsh": ("too much top: presence and treble up, nothing cutting the fizz", [
        Check("amp_presence_high", _amp(AMP["presence"]), lambda v: v >= 7, "rule 9",
              "amp presence is high"),
        Check("amp_treble_high", _amp(AMP["treble"]), lambda v: v >= 7, "rule 9",
              "amp treble is high"),
        Check("amp_high_cut_high", _amp(AMP["high_cut"]), lambda v: v > 12000, "rule 9",
              "the amp's high cut is above 12 kHz; fizz is not rolled off", "Hz"),
        Check("cab_bright", _cab_voice_read, lambda v: v == "bright", "rule 9",
              "the cab name reads bright"),
    ]),
    "fizzy": ("fizz above the guitar's range", [
        Check("amp_high_cut_high", _amp(AMP["high_cut"]), lambda v: v > 12000, "rule 9",
              "the amp's high cut is above 12 kHz", "Hz"),
        Check("amp_gain_high", _amp(AMP["gain"]), lambda v: v >= 8.5, "rule 3",
              "amp gain is very high; fizz rises with gain"),
        Check("amp_presence_high", _amp(AMP["presence"]), lambda v: v >= 7, "rule 9",
              "amp presence is high"),
    ]),
    "dark": ("no top end", [
        Check("amp_treble_low", _amp(AMP["treble"]), lambda v: v <= 3, "rule 9",
              "amp treble is low"),
        Check("amp_presence_low", _amp(AMP["presence"]), lambda v: v <= 3, "rule 9",
              "amp presence is low"),
        Check("amp_high_cut_low", _amp(AMP["high_cut"]), lambda v: v < 5000, "rule 9",
              "the amp's high cut is below 5 kHz", "Hz"),
        Check("cab_dark", _cab_voice_read, lambda v: v == "dark", "rule 9",
              "the cab name reads dark"),
    ]),
    "buried": ("does not cut through the mix", [
        Check("amp_mid_low", _amp(AMP["mid"]), lambda v: v <= 3, "rule 19",
              "amp mid is scooped; mids are what a mix hears"),
        Check("amp_presence_low", _amp(AMP["presence"]), lambda v: v <= 3, "rule 9",
              "amp presence is low"),
        Check("amp_level_low", _amp(AMP["master"]), lambda v: v <= 3, "rule 4",
              "amp master is low"),
        Check("no_eq", _eq_engaged, lambda v: v is False, "rule 17",
              "no EQ block is engaged to carve a place in the mix"),
    ]),
}

DIRECTIONS: dict[str, list[str]] = {
    "muddy": ["raise the amp's low cut toward 80-100 Hz", "pull amp bass toward 5",
              "bring reverb and delay mix under 30 percent on the rhythm"],
    "boomy": ["raise the amp's low cut toward 80-100 Hz", "pull amp depth back", "pull amp bass toward 5"],
    "thin": ["bring amp mid up toward 5-6", "engage a drive in front with low gain and level up",
             "try a 4x12 cab"],
    "harsh": ["set the amp's high cut near 8-10 kHz", "pull presence toward 5", "pull treble toward 5"],
    "fizzy": ["set the amp's high cut near 8-10 kHz", "pull amp gain back a point", "pull presence toward 5"],
    "dark": ["bring treble and presence toward 6", "raise the amp's high cut above 10 kHz",
             "try a brighter cab (V30 or a 57 close mic)"],
    "buried": ["bring amp mid up toward 6", "add a parametric EQ with a small bump around 1 to 2 kHz",
               "bring the amp master up"],
}


def symptoms() -> list[str]:
    return sorted(SYMPTOMS)


def diagnose(cap: dict, symptom: str, reg) -> dict:
    """{symptom, likely, cleared, not_readable, advice}. Every check in the
    symptom's table answers; nothing is skipped silently. An unknown symptom
    raises ValueError naming the ones that exist."""
    key = (symptom or "").strip().lower()
    if key not in SYMPTOMS:
        raise ValueError(f"no diagnosis for {symptom!r}; known symptoms: "
                         + ", ".join(symptoms()))
    _summary, checks = SYMPTOMS[key]
    likely: list[Cause] = []
    cleared: list[Cause] = []
    unread: list[Cause] = []
    for c in checks:
        try:
            v = c.read(reg, cap)
        except Exception:
            v = None
        if v is None:
            unread.append(Cause(c.id, "not readable", None, c.rule,
                                f"{c.id.replace('_', ' ')}: not in this capture"))
            continue
        shown = f"{_fmt(v)}{(' ' + c.unit) if c.unit else ''}"
        if c.predicate(v):
            likely.append(Cause(c.id, "likely", v, c.rule, f"{c.wording} (reads {shown})"))
        else:
            cleared.append(Cause(c.id, "cleared", v, c.rule,
                                 f"{c.id.replace('_', ' ')}: reads {shown}, not the cause"))
    advice = DIRECTIONS[key][:3] if likely else []
    return {"symptom": key, "likely": [x.as_dict() for x in likely],
            "cleared": [x.as_dict() for x in cleared],
            "not_readable": [x.as_dict() for x in unread],
            "advice": advice}


# --- chat routing (the three question shapes) ----------------------------------------

_GREET = r"^\s*(?:(?:hi|hey|hello|ok|okay|so|please|tonecommand)[,:!\s]+)*"
_OBJ = r"(.+?)"
_END = r"\s*[?.!]*\s*$"
#: The three shapes, exactly as REQ-005 states them. Anchored at the start
#: (after an optional greeting) and at the end, so a sentence that merely
#: contains the words is not a question of this kind.
#:  (3) why (does|is) my <scene> (sound|so) <symptom>
RE_DIAGNOSE = re.compile(_GREET + r"why\s+(?:does|is)\s+my\s+" + _OBJ
                         + r"\s+(?:sound|so)\s+([a-z]+)" + _END, re.I)
#:  (1) [what's the] difference between <X> and <Y>
#:      or [how do] <X> and <Y> differ
RE_COMPARE = re.compile(_GREET + r"(?:what(?:'s| is)\s+the\s+)?difference\s+between\s+"
                        + _OBJ + r"\s+and\s+" + _OBJ + _END, re.I)
RE_DIFFER = re.compile(_GREET + r"(?:how\s+do\s+)?" + _OBJ + r"\s+and\s+" + _OBJ
                       + r"\s+differ" + _END, re.I)
#:  (2) [how do I get/make] <X> closer to <Y>, or closer to <Y>
#: An object never starts with a verb: 'bring scene 1 closer to scene 2' is
#: not one of the stated forms and must not slip through as X='bring scene 1'.
_NOT_VERB = r"(?!(?:how|bring|move|push|pull|take|get|make|put|nudge)\b)"
RE_GAP = re.compile(_GREET + r"(?:how\s+(?:do|can|would)\s+i\s+)?(?:(?:get|make)\s+)?"
                    + r"(?:" + _NOT_VERB + _OBJ + r"\s+)?closer\s+to\s+" + _OBJ + _END, re.I)


def parse_question(text: str) -> dict | None:
    """Which of the three shapes a message is, with its objects, or None.
    Precedence: diagnose, compare, gap. Objects are returned raw; the
    caller resolves them and treats an unresolvable object as no route."""
    t = (text or "").strip()
    m = RE_DIAGNOSE.match(t)
    if m and m.group(2).lower() in SYMPTOMS:
        return {"kind": "diagnose", "scene": m.group(1).strip(), "symptom": m.group(2).lower()}
    m = RE_COMPARE.match(t) or RE_DIFFER.match(t)
    if m:
        return {"kind": "compare", "a": m.group(1).strip(), "b": m.group(2).strip()}
    m = RE_GAP.match(t)
    if m and m.group(2):
        return {"kind": "gap", "a": (m.group(1) or "").strip() or None, "b": m.group(2).strip()}
    return None


def findings_text(kind: str, payload: dict) -> str:
    """The block prepended to the model's device state so its prose rests on
    numbers. Fixed heading, so a test can find it."""
    lines = ["ADVISORY FINDINGS (measured)", f"kind: {kind}"]
    if kind == "compare":
        lines += payload.get("lines", [])
    elif kind == "gap":
        lines += [f"- {a['block']} {a['instance']} {a['param']}: {_fmt(a['frm'])} -> {_fmt(a['to'])} ({a['why']})"
                  for a in payload.get("advice", [])]
        lines.append("These are advice. Nothing has been changed. Offer to build only if asked.")
    elif kind == "diagnose":
        lines.append(f"symptom: {payload.get('symptom')}")
        lines += [f"- likely: {c['wording']} [{c['rule']}]" for c in payload.get("likely", [])]
        lines += [f"- cleared: {c['wording']}" for c in payload.get("cleared", [])]
        lines += [f"- {c['wording']}" for c in payload.get("not_readable", [])]
        lines += [f"- try: {d}" for d in payload.get("advice", [])]
        lines.append("Advice only. Do not propose actions.")
    return "\n".join(lines) + "\n"
