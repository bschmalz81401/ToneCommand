"""Read a Neural Amp Modeler capture file (.nam) into a record the planner and
the cab step can use (issue #144, the I1 story of the NAM epic #140).

A .nam file is JSON per the published model-file spec
(https://neural-amp-modeler.readthedocs.io/en/latest/model-file.html):
`version`, `architecture`, `config`, `weights` (a flat list of floats) and an
OPTIONAL `metadata` object whose every key is optional too. This reader uses
the standard library only: no PyTorch, no neural-amp-modeler package, and it
never runs the model. It reads what the file says about itself and nothing
more.

The standing rule (no invented specs): a field the file does not carry is
None in the record and reads "not on file" in the description. Nothing is
guessed from the file name, the folder, or the architecture.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

#: metadata.gear_type -> whether the capture still needs a cab. Full-rig
#: captures include the speaker; everything else, and a MISSING gear_type,
#: needs an IR (the safe reading: an amp with no cab is a fizzy mistake,
#: a cab on a full-rig capture is a filter, and the file said nothing).
GEAR_TYPE_NEEDS_CAB = {
    "amp": True, "pedal": True, "pedal_amp": True, "preamp": True,
    "amp_cab": False, "amp_pedal_cab": False, "studio": False,
}

#: metadata.tone_type -> the standard scene role (kb/HARDWARE_RULES.md
#: "Standard scene layout"; fm9.tone_review.infer_role's vocabulary). An
#: opinionated table, kept in one place so it can be argued with.
TONE_TYPE_ROLE = {
    "clean": "clean", "overdrive": "rhythm", "crunch": "rhythm",
    "fuzz": "rhythm", "hi_gain": "lead",
}

METADATA_FIELDS = ("gear_make", "gear_model", "name", "modeled_by", "date",
                   "gear_type", "tone_type", "input_level_dbu", "output_level_dbu")

NOT_ON_FILE = "not on file"


class NamError(ValueError):
    """The file is not a .nam capture this reader can use; one line says why."""


@dataclass
class CaptureRecord:
    architecture: str
    config_summary: str
    config: dict
    version: str | None
    weights: int
    gear_make: str | None = None
    gear_model: str | None = None
    name: str | None = None
    modeled_by: str | None = None
    date: str | None = None
    gear_type: str | None = None
    tone_type: str | None = None
    input_level_dbu: float | None = None
    output_level_dbu: float | None = None
    metadata_present: bool = False
    on_file: list[str] = field(default_factory=list)

    @property
    def needs_cab(self) -> bool:
        """True unless the file says the capture already includes the cab."""
        return GEAR_TYPE_NEEDS_CAB.get(self.gear_type or "", True)

    @property
    def scene_role(self) -> str | None:
        return TONE_TYPE_ROLE.get(self.tone_type or "")

    @property
    def gear(self) -> str | None:
        """'Make Model' from what is on file, or None when neither is."""
        parts = [p for p in (self.gear_make, self.gear_model) if p]
        return " ".join(parts) if parts else None

    def describe(self) -> str:
        """One line, every absent field said to be absent, nothing inferred."""
        gear = self.gear or f"gear {NOT_ON_FILE}"
        by = f" by {self.modeled_by}" if self.modeled_by else f", modeled_by {NOT_ON_FILE}"
        tone = self.tone_type or f"tone type {NOT_ON_FILE}"
        if self.gear_type is None:
            cab = f"needs a cab (gear type {NOT_ON_FILE})"
        else:
            cab = "needs a cab" if self.needs_cab else "full rig, no cab needed"
        levels = (f"in {self.input_level_dbu} dBu, out {self.output_level_dbu} dBu"
                  if self.input_level_dbu is not None and self.output_level_dbu is not None
                  else f"levels {NOT_ON_FILE}")
        return f"capture of {gear}{by}; {tone}; {cab}; {self.architecture} {self.config_summary}; {levels}"

    def as_dict(self) -> dict:
        return {
            "architecture": self.architecture, "config_summary": self.config_summary,
            "config": self.config, "version": self.version, "weights": self.weights,
            **{k: getattr(self, k) for k in METADATA_FIELDS},
            "metadata_present": self.metadata_present, "on_file": list(self.on_file),
            "needs_cab": self.needs_cab, "scene_role": self.scene_role,
            "gear": self.gear, "line": self.describe(),
        }


def _config_summary(architecture: str, config: dict) -> str:
    """What the config says about size, in the file's own numbers. WaveNet
    files carry `layers` (a list of layer groups with `channels` and
    `dilations`); LSTM files carry `hidden_size` and `num_layers`. Anything
    else is reported as the config's own keys, never as a guess."""
    if not isinstance(config, dict):
        return "config not on file"
    arch = architecture.lower()
    if arch == "wavenet" and isinstance(config.get("layers"), list):
        groups = [g for g in config["layers"] if isinstance(g, dict)]
        dil = sum(len(g.get("dilations") or []) for g in groups)
        ch = sorted({g.get("channels") for g in groups if isinstance(g.get("channels"), int)})
        return f"({len(groups)} layer groups, {dil} layers, channels {'/'.join(str(c) for c in ch) or NOT_ON_FILE})"
    if arch == "lstm":
        hs, nl = config.get("hidden_size"), config.get("num_layers")
        return f"(hidden size {hs if hs is not None else NOT_ON_FILE}, layers {nl if nl is not None else NOT_ON_FILE})"
    keys = ", ".join(sorted(str(k) for k in config)) or "empty"
    return f"(config keys: {keys})"


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def read_nam(data: bytes | str) -> CaptureRecord:
    """Parse .nam bytes (or text). Raises NamError with one line on refusal."""
    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise NamError("not a .nam file: not UTF-8 text") from None
    else:
        text = data
    try:
        doc = json.loads(text)
    except ValueError as exc:
        raise NamError(f"not a .nam file: invalid JSON ({str(exc).split(':')[0]})") from None
    if not isinstance(doc, dict):
        raise NamError("not a .nam file: the top level is not a JSON object")
    architecture = doc.get("architecture")
    if not isinstance(architecture, str) or not architecture.strip():
        raise NamError("not a .nam file: no architecture")
    weights = doc.get("weights")
    if not isinstance(weights, list):
        raise NamError("not a .nam file: no weights list")
    config = doc.get("config") if isinstance(doc.get("config"), dict) else None
    version = doc.get("version") if isinstance(doc.get("version"), str) else None
    meta = doc.get("metadata")
    metadata_present = isinstance(meta, dict)
    meta = meta if metadata_present else {}
    record = CaptureRecord(architecture=architecture.strip(), config_summary=_config_summary(architecture, config),
                           config=config if config is not None else {}, version=version, weights=len(weights),
                           metadata_present=metadata_present)
    for key in METADATA_FIELDS:
        value = meta.get(key)
        if key.endswith("_dbu"):
            value = _number(value)
        elif not isinstance(value, str) or not value.strip():
            value = None
        else:
            value = value.strip()
        if value is not None:
            setattr(record, key, value)
            record.on_file.append(key)
    return record


def read_nam_file(path: str | Path) -> CaptureRecord:
    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError as exc:
        raise NamError(f"cannot read {p.name}: {exc.strerror or exc}") from None
    return read_nam(data)
