"""The sentinel handle for the #111 substitution proof.

A device that DECLINES every capability gate and RAISES if any gated method
is reached anyway. Installed in place of the simulator and driven through
every route, it turns "did every call site check the gate" from a review
question into a test: a gated call that slipped past the gate is an
AssertionError, a decline that a broad `except Exception` swallowed is a 500
or a silent 200 where a 409 was owed, and both are visible per route.

The 16 gated methods are read off fm9.adapter.CAPABILITY_PROTOCOLS at import
time, never hand-listed, so a method added to a sub-Protocol is guarded here
the moment it is declared. Everything else delegates to a real second device
(the simulator), so ungated reads behave exactly as before and the only
variable under test is the declaration.

The same class, handed accepting Capabilities and a `record` list, is the
recording variant the invariant tests use: every call that reaches the device
is logged in order, so "the capability check ran BEFORE any write" and "the
undo snapshot came first" are read off the log rather than argued.
"""
from __future__ import annotations

from fm9.adapter import (CAPABILITY_PROTOCOLS, Capabilities, ReadPath,
                         Topology)

#: method name -> gate label, derived from the sub-Protocols.
GATED_METHODS: dict[str, str] = {
    name: label
    for label, _gate, proto in CAPABILITY_PROTOCOLS
    for name in proto.__protocol_attrs__
}

#: The predicate for each gate, by label.
GATE_PREDICATES = {label: gate for label, gate, _ in CAPABILITY_PROTOCOLS}

#: Declines every gate. `Topology` has no NONE member: FIXED is its bottom
#: rung ("no say over the chain at all"), which is the declared-nothing
#: state, and every gate flag is False. The read path is honest so the
#: ungated reads it delegates are not labelled unverified for no reason.
DECLINE_ALL = Capabilities(
    read_path=ReadPath.DEVICE,
    reads_slot_names=True,
    reads_slot_state=True,
    verifies_writes=True,
    has_scenes=True,
    stores_presets=True,
    topology=Topology.FIXED,
    has_modifiers=False,
    installs_files=False,
    can_rename=False,
    composable_scene_slots=False,
)


class GatedMethodFired(AssertionError):
    """A gated method was reached on a device that declined its gate."""


def _gated(name: str, label: str):
    def method(self, *args, **kwargs):
        if not GATE_PREDICATES[label](self._caps):
            self.fired.append(name)
            raise GatedMethodFired(f"gated method fired: {name}")
        return self._call(name, args, kwargs)
    method.__name__ = name
    return method


class SentinelDevice:
    """See the module docstring.

    `inner` answers every ungated name. `capabilities` defaults to declining
    everything; pass the inner device's own to get the recording variant.
    `record`, when given, receives (name, args, kwargs) for every call that
    reaches the inner device, in order.
    """

    def __init__(self, inner, capabilities: Capabilities = DECLINE_ALL,
                 record: list | None = None):
        self._inner = inner
        self._caps = capabilities
        self._record = record
        self.fired: list[str] = []

    def capabilities(self) -> Capabilities:
        return self._caps

    def _call(self, name, args, kwargs):
        if self._record is not None:
            self._record.append((name, args, kwargs))
        return getattr(self._inner, name)(*args, **kwargs)

    def __getattr__(self, name):
        # Only reached for names not defined on the class, i.e. the ungated
        # surface. Delegated dynamically so a patched inner method is seen.
        attr = getattr(self._inner, name)
        if callable(attr) and self._record is not None and \
                not name.startswith("__"):
            def recorded(*args, **kwargs):
                return self._call(name, args, kwargs)
            return recorded
        return attr


for _name, _label in GATED_METHODS.items():
    setattr(SentinelDevice, _name, _gated(_name, _label))
