"""The device adapter contract (ARCHITECTURE.md, step 1 of the migration).

Any device family ToneCommand supports satisfies this Protocol. The FM9
device and its simulator are certified against it by tests; new devices
(HeadRush, ToneX, Kemper, your fridge) implement the same surface and
inherit the invariant safety layer above it.

This is a typing.Protocol, not a base class: existing device code is not
forced to inherit anything, it just has to actually provide the surface.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DeviceAdapter(Protocol):
    """The swappable layer. Everything above this is device-blind."""

    def status_dump(self) -> Any:
        """Current blocks/bypass/channel state from an honest read path."""
        ...

    def current_preset(self) -> Any:
        """(number, name) of the active preset, or None if unreachable."""
        ...

    def select_preset(self, number: int) -> Any: ...

    def set_scene(self, scene: int) -> Any: ...

    def set_bypass(self, effect_id: int, bypassed: bool) -> Any: ...

    def set_channel(self, effect_id: int, channel_0based: int) -> Any: ...

    def set_param_display(self, spec: Any, display_value: float) -> Any:
        """Verified write: settle, read back, report before/after."""
        ...

    def set_param_ordinal(self, spec: Any, ordinal: int) -> Any: ...

    def bulk_read(self, effect_id: int) -> Any: ...

    def store_preset(self, slot: int) -> Any:
        """Whitelisted, confirmation-gated persistence. Must refuse
        non-whitelisted targets."""
        ...

    def close(self) -> Any: ...
