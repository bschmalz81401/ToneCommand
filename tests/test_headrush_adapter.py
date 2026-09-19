"""Issue #125: the HeadRush DeviceAdapter, proven against the committed
simulator through the real client. No hardware; every claim about the unit
comes from docs/HEADRUSH-HARDWARE-FINDINGS.md and is cited in the adapter.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from devices.headrush import adapter as hr
from devices.headrush import registry as hr_registry
from devices.headrush.client import HeadrushClient
from devices.headrush.registry import NotMeasured
from devices.headrush.sim import HeadrushSim
from fm9.adapter import (Capabilities, ReadPath, SceneSlotState, Topology,
                         conformance)

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "config" / "headrush_schema.json").read_text())


class RecordingOpener:
    """Counts what reaches the wire, and can misbehave on purpose."""
    def __init__(self, sim, *, revert=None, hide=None):
        self.sim = sim
        self.calls = []
        self.revert = revert or {}     # (path, name) -> value the unit reverts to
        self.hide = set(hide or ())    # object paths that 404 on read

    def __call__(self, url, method, body, headers, timeout):
        self.calls.append((method, url))
        out = self.sim.opener(url, method, body, headers, timeout)
        path = url.split("/api/v1", 1)[1]
        if method == "PUT" and path.startswith("/object-properties"):
            target = path[len("/object-properties"):]
            for (p, name), value in self.revert.items():
                if p == target and name in json.loads(body or b"{}"):
                    self.sim.set_properties(p, {name: value})
        if method == "GET" and path.startswith("/object-properties"):
            target = path[len("/object-properties"):]
            if target in self.hide:
                import urllib.error
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return out


@pytest.fixture
def reg():
    return hr_registry.load()


def make(reg, **kw):
    sim = HeadrushSim()
    opener = RecordingOpener(sim, **kw)
    client = HeadrushClient("sim.local", "127.0.0.1", opener=opener)
    adapter = hr.HeadrushAdapter(client, reg, sleep=lambda s: None)
    return sim, opener, adapter


# --- conformance and capabilities ---------------------------------------------

def test_the_adapter_conforms_with_no_problems(reg):
    _sim, _op, a = make(reg)
    assert conformance(hr.HeadrushAdapter) == []
    assert conformance(a) == []


def test_capabilities_are_the_measured_ones(reg):
    _sim, _op, a = make(reg)
    c = a.capabilities()
    assert c == Capabilities(
        read_path=ReadPath.DEVICE, observes_foreign_writes=True,
        reads_slot_names=True, reads_slot_state=False, verifies_writes=True,
        has_scenes=True, stores_presets=False, topology=Topology.SELECTED,
        has_modifiers=False, installs_files=False, can_rename=False,
        composable_scene_slots=True)
    assert c.can_verify


def test_contract_methods_with_no_headrush_meaning_say_so(reg):
    _sim, _op, a = make(reg)
    with pytest.raises(hr.NotSupported):
        a.set_channel(1, 0)
    with pytest.raises(hr.NotSupported):
        a.store_preset(3)
    spec = reg.resolve("Amp", "Bass")
    with pytest.raises(NotMeasured):
        a.set_param_display(spec, 75.0)
    with pytest.raises(NotMeasured):
        a.get_param_display(spec)


# --- topology, chain, scenes ----------------------------------------------

def test_topology_is_enumerated_named_and_chosen_by_index(reg):
    sim, _op, a = make(reg)
    assert len(a.topologies()) == 10 and a.topologies()[0] == "S"
    assert a.select_topology(3)["ok"] and a.current_topology() == 3 == sim.routing
    with pytest.raises(LookupError):
        a.select_topology(42)


def test_place_by_slot_and_reorder_by_swap(reg):
    sim, _op, a = make(reg)
    assert a.place_block(2, 19)["ok"] and sim.slot(2) == 19
    assert a.place_block(5, 3)["ok"] and sim.slot(5) == 3
    assert a.reorder_block(2, 5)["ok"]
    assert (sim.slot(2), sim.slot(5)) == (3, 19)
    assert a.place_block(2, 0)["ok"] and sim.slot(2) == 0
    with pytest.raises(ValueError):
        a.place_block(15, 19)


def test_scenes_are_tri_state_by_slot_name(reg):
    sim, _op, a = make(reg)
    assert a.scene_slots(1) == {}
    assert a.set_scene_slot(1, "Amp", SceneSlotState.ON)["ok"]
    assert a.set_scene_slot(1, "Delay", SceneSlotState.OFF)["ok"]
    assert a.scene_slots(1) == {"Amp": SceneSlotState.ON, "Delay": SceneSlotState.OFF}
    assert a.set_scene_slot(1, "Amp", SceneSlotState.NO_CHANGE)["ok"]
    assert a.scene_slots(1) == {"Amp": SceneSlotState.NO_CHANGE,
                                "Delay": SceneSlotState.OFF}


def test_set_scene_needs_scene_mode_and_reads_last_scene_back(reg):
    sim, _op, a = make(reg)
    r = a.set_scene(2)
    assert r["ok"] is False and "scene mode" in r["detail"]
    sim.set_properties(hr.FOOTSWITCH, {"ModeNew2": 2})
    sim.set_properties(hr.FOOTSWITCH, {"LastScene": 1})     # the unit's answer
    assert a.set_scene(2)["ok"]


def test_readback_scene_activation_goes_through_the_verified_write_path(reg):
    """Review round 1: SceneActive is a property write like any other, so it
    is read back through _write_verified and reported. Success, though, is
    the measured effect (LastScene, finding 4): whether SceneActive persists
    or is a pulse the unit clears is unmeasured, so a cleared flag with an
    engaged scene is reported as engaged, with the flag noted as undecoded."""
    sim, opener, a = make(reg, revert={(hr.FOOTSWITCH, "SceneActive2"): False})
    sim.set_properties(hr.FOOTSWITCH, {"ModeNew2": 2, "LastScene": 1})
    r = a.set_scene(2)
    assert r["ok"] is True and r["written"] is False and r["engaged"] is True
    assert "SceneActive2" in r["detail"] and any("latches" in u for u in a.undecoded)
    puts = [u for m, u in opener.calls if m == "PUT"]
    assert len(puts) == 1 and "FootSwitch" in puts[0], "one verified write"
    sim2, _op, a2 = make(reg)
    sim2.set_properties(hr.FOOTSWITCH, {"ModeNew2": 2})
    r = a2.set_scene(2)                       # written, but LastScene never moved
    assert r["ok"] is False and r["written"] is True and r["engaged"] is False


def test_bypass_writes_the_blocks_own_on_switch(reg):
    sim, _op, a = make(reg)
    a.place_block(1, 19)
    r = a.set_bypass(1, True)
    assert r["ok"] and sim.get_properties("/Evil/Engine/Patch/Neural_Amp_Modeler")["On"] is False


def test_status_and_rig_names(reg):
    sim, _op, a = make(reg)
    sim.set_properties(hr.RIGS, {"RigNames": ["Init Rig", "Gig"]})
    st = a.status_dump()
    assert st["rig"] == "Init Rig" and set(st["slots"]) == set(range(1, 15))
    assert a.current_preset() == (None, "Init Rig")
    assert a.slot_name(1) == "Gig" and a.is_slot_empty(7)
    assert a.scan_slots(0, 1) == [(0, "Init Rig"), (1, "Gig")]


# --- readback: every write is verified after a settle -----------------------

def test_readback_every_property_write_is_reread_after_the_settle(reg):
    waits = []
    sim = HeadrushSim()
    opener = RecordingOpener(sim)
    a = hr.HeadrushAdapter(HeadrushClient("sim.local", "127.0.0.1", opener=opener),
                           reg, settle_s=0.5, sleep=waits.append)
    spec = reg.resolve("Amp", "Bass")
    r = a.set_param_wire(spec, 0.25)
    assert r["ok"] and r["read"] == 0.25
    assert waits == [0.5], "one settle per write, of the measured length"
    puts = [u for m, u in opener.calls if m == "PUT"]
    gets = [u for m, u in opener.calls if m == "GET"]
    assert len(puts) == 1 and any("/Evil/Engine/Patch/Amp" in g for g in gets)


def test_readback_default_settle_is_at_least_half_a_second(reg):
    sim = HeadrushSim()
    a = hr.HeadrushAdapter(HeadrushClient("sim.local", "127.0.0.1", opener=sim.opener), reg)
    assert a.settle_s >= 0.5


def test_readback_catches_a_write_the_unit_silently_reverts(reg):
    """Finding 1, ordinal 4: acknowledged, then put back to 0 by the unit."""
    sim, _op, a = make(reg, revert={(hr.CHAIN, "ModuleType3"): 0})
    r = a.place_block(3, 4)
    assert r["ok"] is False and "not placed" in r["detail"] and "reads 0" in r["detail"]


def test_readback_alone_is_not_enough_the_object_must_answer(reg):
    """Finding 1, the 254 shape: the value sticks but no object exists.
    Simulated with a backed ordinal whose object is hidden."""
    sim, _op, a = make(reg, hide={"/Evil/Engine/Patch/Neural_Amp_Modeler"})
    r = a.place_block(3, 19)
    assert r["ok"] is False and "publishes no object" in r["detail"]
    assert sim.slot(3) == 19, "the value did land; the object is what is missing"


def test_readback_reports_a_mismatch_instead_of_success(reg):
    sim, _op, a = make(reg, revert={(hr.CHAIN, "Routing"): 0})
    r = a.select_topology(4)
    assert r["ok"] is False and "wrote 4" in r["detail"]


# --- allowlist: deny by default, before transport ----------------------------

def test_allowlist_refuses_before_transport(reg):
    _sim, opener, a = make(reg)
    n = len(opener.calls)
    for path, method in [("/Evil/API/Rigs", "deleteRig"), ("/Evil/Engine/GlobalEQMain", "reset"),
                         ("/Evil/API/Rigs", "makeNewRig"), ("/Evil/System", "factoryReset")]:
        with pytest.raises(hr.MethodRefused):
            a.call_method(path, method, [])
    assert len(opener.calls) == n, "a refused method never reached the opener"


def test_allowlist_contains_only_load_rig(reg):
    assert hr.ALLOWED_METHODS == frozenset({("/Evil/API/Rigs", "loadRig")})


def test_allowlist_lets_nothing_dangerous_through_the_published_method_surface():
    published = []
    for path, h in SCHEMA["paths"].items():
        for m in (SCHEMA["metas"][h].get("x-methods") or {}):
            published.append((path, m))
    assert len(published) > 50, "fixture sanity: the schema publishes methods"
    bad = ("firmware", "update", "recover", "reset", "factory", "format",
           "reboot", "delete", "erase", "flash", "upgrade")
    dangerous = [pm for pm in published if any(b in pm[1].lower() for b in bad)]
    assert dangerous, "fixture sanity: there are dangerous names to keep out"
    assert not (set(dangerous) & hr.ALLOWED_METHODS)
    assert hr.ALLOWED_METHODS <= set(published) | {("/Evil/API/Rigs", "loadRig")}


def test_allowlist_the_one_allowed_method_reaches_the_wire(reg):
    _sim, opener, a = make(reg)
    n = len(opener.calls)
    assert a.call_method("/Evil/API/Rigs", "loadRig", ["rig-0000", ""]) is True
    assert len(opener.calls) == n + 1 and "object-method" in opener.calls[-1][1]


# --- #135: select_preset loads by rig ID, found by the #126 hardware pass ---------

def test_select_preset_resolves_a_name_to_the_rig_id_and_settles_before_reading(reg):
    waits = []
    sim = HeadrushSim(rigs=["Init Rig", "Gig", "##HRB ToneCommandTesting"])
    opener = RecordingOpener(sim)
    a = hr.HeadrushAdapter(HeadrushClient("sim.local", "127.0.0.1", opener=opener), reg,
                           settle_s=0.5, sleep=waits.append)
    r = a.select_preset("Gig")
    assert r["ok"] is True and r["loaded"] == "Gig" and r["rig_id"] == "rig-0001", r
    assert r["returned"] is True
    assert waits == [0.5], "loadRig returns before the engine swaps: settle before the read-back"
    assert sim.loaded_rig == "Gig"
    # an id works directly too
    r = a.select_preset("rig-0002")
    assert r["ok"] and r["loaded"] == "##HRB ToneCommandTesting"


def test_select_preset_sends_the_id_never_the_name(reg):
    """The exact defect: a NAME in loadRig's first argument gets 504 on the
    Core and loads nothing. The simulator answers the same way, so the
    adapter cannot regress to sending the name without this test going red."""
    sim = HeadrushSim(rigs=["Init Rig", "Gig"])
    sent = []
    real = sim.opener

    def opener(url, method, body, headers, timeout):
        if "/object-method" in url:
            sent.append(json.loads(body or b"{}").get("arguments"))
        return real(url, method, body, headers, timeout)

    a = hr.HeadrushAdapter(HeadrushClient("sim.local", "127.0.0.1", opener=opener), reg, sleep=lambda s: None)
    assert a.select_preset("Gig")["ok"]
    assert sent == [["rig-0001", ""]], sent
    with pytest.raises(Exception, match="504"):
        a.client.call_method("/Evil/API/Rigs", "loadRig", ["Gig", ""])


def test_select_preset_refuses_an_unknown_rig_before_transport(reg):
    _sim, opener, a = make(reg)
    n = len(opener.calls)
    with pytest.raises(LookupError, match="no rig"):
        a.select_preset("No Such Rig")
    assert not any("object-method" in u for _m, u in opener.calls[n:])


def test_allowlist_refused_module_ordinals_never_reach_the_wire(reg):
    _sim, opener, a = make(reg)
    n = len(opener.calls)
    for ordinal in (20, 254):
        with pytest.raises(PermissionError, match="finding 1"):
            a.place_block(3, ordinal)
    assert len(opener.calls) == n
    assert set(hr.REFUSED_MODULE_ORDINALS) == {20, 254}, "4 is the unit's own refusal"


# --- evidence ------------------------------------------------------------------

def test_evidence_names_the_measured_unit_and_the_unverified_models(reg):
    _sim, _op, a = make(reg)
    e = a.evidence()
    assert e["model"] == "HeadRush Core" and e["firmware"] == "5.1.0.2a63755"
    assert e["measured_on"] == "2026-09-15"
    assert set(e["unverified_models"]) == {"Prime", "Flex Prime"}
    assert (ROOT / e["source"]).exists()
    text = (ROOT / e["source"]).read_text()
    assert e["firmware"] in text and "Prime and Flex Prime are UNVERIFIED" in text


def test_evidence_firmware_label_reads_the_units_own_version(reg):
    sim, _op, a = make(reg)
    sim.set_properties(hr.GUI, {"AppVersion": "5.1.0.2a63755"})
    assert a.firmware_label() == "5.1.0.2a63755"


def test_evidence_capabilities_docstring_cites_the_findings():
    doc = hr.__doc__
    assert "HEADRUSH-HARDWARE-FINDINGS" in doc and "5.1.0.2a63755" in doc
    for flag in ("reads_slot_state", "stores_presets", "composable_scene_slots",
                 "has_modifiers", "installs_files", "can_rename"):
        assert flag in doc, f"{flag} is declared without its reason"
    assert "Prime" not in hr.__doc__.split("WHAT IT REFUSES")[1], \
        "nothing below the evidence section claims Prime behaviour"
