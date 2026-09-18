"""Issue #124: one selected device context pairs the active adapter with its
own registry, and everything that used to read the one global FM9 registry
now reads the selected one.

Injection goes through server.use_device(); nothing here patches the FM9
class. The default context is the FM9 exactly as before, which the existing
suites prove on every run; these tests cover the seam itself.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import server
from fm9.adapter import Capabilities, ReadPath, Topology
from fm9.sim import SimFM9
from tests.stub_device import StubDevice


class FakeRegistry:
    """A registry shaped nothing like the FM9's, so a lookup that reached
    the FM9 catalog would be visible as a wrong answer, not a coincidence."""
    def __init__(self):
        self.resolved = []
        self.params = {}

    def resolve_block(self, name, instance=1):
        self.resolved.append((name, instance))
        if name.lower() == "amp":
            return "STUBAMP", 900
        raise ValueError(f"unknown block {name!r} on the stub")

    def find_param(self, fam, name):
        return None

    def spec(self, fam, pid, instance=1):
        raise LookupError


def test_the_default_context_is_the_fm9_and_its_registry():
    ctx = server.device_context()
    assert ctx.kind == "fm9" and ctx.registry is server.FM9_REGISTRY
    assert server.reg.effect_id("DISTORT") == server.FM9_REGISTRY.effect_id("DISTORT")


def test_use_device_pairs_one_adapter_with_one_registry_and_restores():
    stub, fake = StubDevice(), FakeRegistry()
    before = server.device_context()
    with server.use_device("stub", stub, fake) as ctx:
        assert server.device_context() is ctx
        assert ctx.adapter is stub and ctx.registry is fake
        assert repr(server.reg) == "<reg -> stub registry>"
        # get_fm9() hands out the selected adapter, gated like the FM9
        handle = server.get_fm9()
        assert isinstance(handle, server.GatedDevice)
        assert handle.capabilities() is StubDevice.CAPABILITIES
    assert server.device_context() is before
    assert server.reg.effect_id("DISTORT") == server.FM9_REGISTRY.effect_id("DISTORT")


def test_validation_resolves_blocks_through_the_selected_registry():
    fake = FakeRegistry()
    with server.use_device("stub", StubDevice(), fake):
        errs, _warn = server.validate_action(
            server.Action(kind="set_bypass", block="amp", bypassed=False))
        assert fake.resolved == [("amp", 1)], "the FM9 catalog was not consulted"
        errs, _warn = server.validate_action(
            server.Action(kind="set_bypass", block="delay", bypassed=False))
        assert errs and "delay" in " ".join(errs).lower()


def test_a_declined_capability_on_the_injected_device_still_answers_409(monkeypatch):
    class NoFiles(StubDevice):
        CAPABILITIES = Capabilities(read_path=ReadPath.DEVICE, verifies_writes=True,
                                    topology=Topology.SELECTED, installs_files=False)

        def capabilities(self):
            return self.CAPABILITIES

    monkeypatch.setattr(server, "_gig_mode", {"on": False})
    monkeypatch.setattr(server, "_install_cache", {"deadbeef": b"x"})
    with server.use_device("stub", NoFiles(), FakeRegistry()):
        r = TestClient(server.app).post(
            "/api/install-cab", json={"hash": "deadbeef", "bank": 1, "number": 1})
        assert r.status_code == 409, r.text
        assert r.json().get("refused") is True


def test_the_one_writer_lock_and_the_digest_gate_survive_injection(monkeypatch):
    lock_before = server._lock
    monkeypatch.setattr(server, "_gig_mode", {"on": False})
    with server.use_device("stub", StubDevice(), FakeRegistry()):
        assert server._lock is lock_before
        r = TestClient(server.app).post("/api/apply", json={
            "actions": [{"kind": "set_bypass", "block": "amp", "bypassed": False}],
            "plan_digest": "not-a-reviewed-revision"})
        body = r.json()
        assert body.get("refused") == "stale_plan", body


def test_fm9_context_keeps_the_lazy_connect_and_the_injected_sim(monkeypatch):
    sim = SimFM9(server.reg)
    monkeypatch.setattr(server, "_fm9", sim)
    handle = server.get_fm9()
    assert object.__getattribute__(handle, "_device") is sim
    monkeypatch.setattr(server, "_fm9", None)
    monkeypatch.setenv("TONECOMMAND_SIM", "1")
    handle = server.get_fm9()
    assert type(object.__getattribute__(handle, "_device")).__name__ == "FM9"


def test_a_context_without_an_adapter_reads_as_not_connected():
    with server.use_device("stub", None, FakeRegistry()):
        with pytest.raises(server.FM9NotFound):
            server.get_fm9()
