"""The HeadRush simulator: no unit, no network, and no invented device model.

#33 phase 3, #121. The acceptance criteria in order: it runs with no hardware,
it is built from the committed schema rather than a handwritten model, rig and
slot and topology and scene behaviour are deterministic, the three path shapes
are not flattened, and unknown behaviour is reported rather than smoothed over.
"""
import json
import socket
import urllib.error

import pytest

from devices.headrush import topology as T
from devices.headrush.client import HeadrushClient
from devices.headrush.sim import CHAIN, RIG, HeadrushSim, SimError


@pytest.fixture
def sim():
    return HeadrushSim()


@pytest.fixture
def client(sim):
    """The REAL client, driven by the sim through the injected opener.

    A simulator that reimplemented the client would agree with itself. This
    way the URL building, the empty-body PUT handling and the error text under
    test are the ones that will run against hardware.
    """
    return HeadrushClient("sim.local", "127.0.0.1", opener=sim.opener)


# --- AC1: no hardware, no network ---------------------------------------

def test_it_runs_with_the_network_unplugged(monkeypatch):
    """Not "we did not notice a socket": sockets are made to raise."""
    def no_sockets(*a, **k):
        raise AssertionError("the simulator opened a socket")

    monkeypatch.setattr(socket, "socket", no_sockets)
    monkeypatch.setattr(socket, "getaddrinfo", no_sockets)
    s = HeadrushSim()
    c = HeadrushClient("sim.local", "127.0.0.1", opener=s.opener)
    assert c.get_property(CHAIN, "Routing") == 0


# --- AC2: built from the committed schema --------------------------------

def test_every_object_comes_from_the_schema(sim):
    schema = json.loads((T.CONFIG / "headrush_schema.json").read_text())
    assert sim.firmware == schema["firmware"]
    assert set(sim._paths) == set(schema["paths"])
    assert len(sim._paths) == schema["object_count"]


def test_property_ranges_are_the_devices_own(sim):
    """Not a number typed into the simulator: Routing's 0..9 is enforced
    because the unit published 0..9."""
    with pytest.raises(SimError) as err:
        sim.set_properties(CHAIN, {"Routing": 10})
    assert "maximum" in str(err.value)
    with pytest.raises(SimError):
        sim.set_properties(CHAIN, {"Routing": -1})


def test_an_unknown_property_is_refused(sim):
    with pytest.raises(SimError, match="no property"):
        sim.set_properties(CHAIN, {"NoSuchThing": 1})


def test_an_object_with_only_methods_is_not_a_malformed_meta(sim):
    """One object on this firmware publishes `properties: null`. It has
    nothing to read, which is different from being broken."""
    empty = [p for p in sim._paths if sim.meta_for(p) == {}]
    assert empty, "if the firmware changed, this test should be revisited"
    assert sim.get_properties(empty[0]) == {}


# --- AC5: unknown paths and methods are reported, not invented -----------

def test_an_unknown_path_is_a_404_not_a_blank_object(sim, client):
    """Through the client, and as the exception PRODUCTION raises.

    The Opener contract says an opener raises urllib's own exceptions. A sim
    raising its own type would let phase 4 be written to catch something
    hardware never throws, so this pins HTTPError rather than SimError.
    """
    with pytest.raises(urllib.error.HTTPError) as err:
        client.get_properties("/Evil/Engine/Patch/NotAThing")
    assert err.value.code == 404
    assert "no object at" in str(err.value)


def test_calling_the_sim_directly_still_raises_its_own_type(sim):
    """SimError is right for the in-process helpers; only the wire boundary
    has to speak urllib."""
    with pytest.raises(SimError):
        sim.get_properties("/Evil/Engine/Patch/NotAThing")


def test_object_method_refuses_and_records_why(sim, client):
    """No method's behaviour is established, and #125 gates these behind a
    deny-by-default allowlist. Answering smoothly would be the wrong help.

    Typed to SimError with its status rather than to Exception: a bare
    `raises(Exception)` here would also pass if `call_method` were renamed and
    the call raised AttributeError, which would prove nothing about refusal.
    """
    with pytest.raises(urllib.error.HTTPError) as err:
        client.call_method("/Evil/Engine/Patch/Chain", "doSomething")
    assert err.value.code == 501
    assert any("object-method" in u for u in sim.undecoded)


# --- AC3: deterministic rig, slot, topology and scene behaviour ----------

def test_topology_selection_round_trips_through_the_real_client(sim, client):
    for index in range(10):
        client.set_property(CHAIN, "Routing", index)
        assert client.get_property(CHAIN, "Routing") == index
        assert sim.topology.index == index


def test_selecting_a_routing_the_device_does_not_offer_raises(sim):
    with pytest.raises(T.UnknownTopology):
        sim.select_topology(10)


def test_slots_hold_what_was_placed(sim):
    sim.place(4, 42)
    sim.place(9, 17)
    assert sim.slot(4) == 42
    assert sim.occupied() == {4: 42, 9: 17}
    assert sim.slot(1) == 0, "an untouched slot is Empty Slot, ordinal 0"


def test_a_slot_outside_the_fourteen_is_refused(sim):
    for bad in (0, 15):
        with pytest.raises(SimError, match="out of range"):
            sim.place(bad, 1)


def test_loading_a_rig_clears_the_chain_and_says_what_it_cannot_restore(sim):
    sim.place(4, 42)
    sim.library.append("Second Rig")
    sim.load_rig("Second Rig")
    assert sim.loaded_rig == "Second Rig"
    assert sim.get_properties(RIG)["PresetName"] == "Second Rig"
    assert sim.occupied() == {}
    assert any("CONTENTS are not modelled" in u for u in sim.undecoded), \
        "a sim with no stored rigs must say so rather than serve an empty one silently"


def test_an_unknown_rig_is_refused(sim):
    with pytest.raises(SimError, match="no rig named"):
        sim.load_rig("Not In The Library")


# --- AC3: scenes are tri-state and addressed by name --------------------

def test_scenes_are_stored_on_the_devices_own_properties(sim):
    """AC2: schema-derived, not a side table. Scene{n}_{m}_Effect and
    Scene{n}_{m}_Mode are real properties on /Evil/Engine/FootSwitch."""
    from devices.headrush.sim import FOOTSWITCH
    sim.set_scene_slot(1, "Amp", "on")
    props = sim.get_properties(FOOTSWITCH)
    assert props["Scene1_1_Effect"] == "Amp"
    assert props["Scene1_1_Mode"] == 1


def test_the_mode_integers_are_the_measured_ones(sim):
    """0/1/2 carry no names on the device. Which is which was measured on a
    Core and cross-read from its bundle; guessing the order would put a
    scene's blocks in exactly the wrong places."""
    from devices.headrush.sim import MODE_VALUE, SLOT_MODE
    assert SLOT_MODE == {0: "no_change", 1: "on", 2: "off"}
    assert MODE_VALUE["on"] == 1 and MODE_VALUE["off"] == 2


def test_a_scene_outside_the_ten_is_refused(sim):
    for bad in (0, 11):
        with pytest.raises(SimError, match="out of range"):
            sim.scene_slots(bad)
        with pytest.raises(SimError, match="out of range"):
            sim.set_scene_slot(bad, "Amp", "on")


def test_a_slot_a_scene_says_nothing_about_is_absent_not_off(sim):
    sim.set_scene_slot(1, "Amp", "on")
    slots = sim.scene_slots(1)
    assert slots == {"Amp": "on"}
    assert "Delay" not in slots, "silence is no_change, which is not off"


def test_setting_no_change_removes_rather_than_stores_a_third_value(sim):
    sim.set_scene_slot(1, "Amp", "off")
    assert sim.scene_slots(1)["Amp"] == "off"
    sim.set_scene_slot(1, "Amp", "no_change")
    assert "Amp" not in sim.scene_slots(1)


def test_scene_slots_are_addressed_by_name(sim):
    with pytest.raises(SimError, match="by name"):
        sim.set_scene_slot(1, "", "on")


def test_an_invalid_scene_state_is_refused(sim):
    with pytest.raises(SimError, match="not a scene slot state"):
        sim.set_scene_slot(1, "Amp", "maybe")


# --- AC4: the three shapes are not flattened ----------------------------

def test_the_same_slot_pair_answers_differently_per_topology(sim):
    """The heart of #121. Slots 3 and 9 are one run on a straight path, two
    parallel branches on a split, and two separate rigs on a dual. A grid
    model would give one answer to all three."""
    sim.select_topology(0)                          # straight
    assert sim.topology.feeds(3, 9) is True

    sim.select_topology(1)                          # split 3-4-3
    assert sim.topology.role(9) is T.SlotRole.BRANCH_B
    assert sim.topology.feeds(4, 9) is False

    sim.select_topology(7)                          # dual 4-10
    assert sim.topology.path_of(3) != sim.topology.path_of(9)
    assert sim.topology.feeds(3, 9) is False


def test_the_sim_serves_only_the_integer_the_device_serves(sim, client):
    """The unit publishes ten names and nothing about their shapes, so the
    sim's wire surface must not publish shapes either. Anything wanting the
    shape asks the topology table, which declares its provenance."""
    chain = client.get_properties(CHAIN)
    assert chain["Routing"] == 0
    assert not any("role" in k.lower() or "branch" in k.lower() for k in chain)
    assert sim.topologies.api_readable is False
