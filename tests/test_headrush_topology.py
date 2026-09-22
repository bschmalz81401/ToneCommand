"""The ten signal-path templates, and the two traps in them.

#33 phase 3, #121. These pin the distinctions the ticket says must not be
flattened: straight, split/rejoin and dual are three different shapes, and the
device's own data structure cannot tell the last two apart on its own.

Nothing here touches hardware or the network.
"""
import dataclasses
import json

import pytest

from devices.headrush import topology as T


@pytest.fixture(scope="module")
def table():
    return T.load()


# --- provenance is not optional ----------------------------------------

def test_the_table_says_it_is_not_a_device_read(table):
    """The shapes come from the vendor's editor, not the API. Anything built
    on this has to be able to say so, which means carrying it."""
    assert table.api_readable is False
    assert table.provenance == "vendor editor bundle"
    assert "not the API" in table.warning or "not a device read" in table.warning.lower()


def test_there_are_exactly_ten(table):
    assert len(table) == 10
    assert sorted(t.index for t in table) == list(range(10))


# --- the three kinds, which is the whole point -------------------------

def test_the_three_shapes_are_distinct(table):
    kinds = {t.index: t.kind for t in table}
    assert kinds[0] is T.TopologyKind.STRAIGHT
    assert kinds[1] is T.TopologyKind.SPLIT       # Middle Split 3-4-3
    assert kinds[7] is T.TopologyKind.DUAL        # Dual Path 4-10
    assert {k for k in kinds.values()} == set(T.TopologyKind)


def test_a_split_partitions_fourteen_with_the_branches_counted_twice(table):
    """The vendor's names state the partition and the roles agree with it:
    3 + (4 | 4) + 3 for SPS-1."""
    sps1 = table.get(1)
    assert sps1.branch_slots(T.SlotRole.BRANCH_A) == (4, 5, 6, 7)
    assert sps1.branch_slots(T.SlotRole.BRANCH_B) == (8, 9, 10, 11)
    common = [n for n, r in enumerate(sps1.roles, 1) if r is T.SlotRole.COMMON]
    assert common == [1, 2, 3, 12, 13, 14]


def test_a_dual_is_two_paths_not_a_branch(table):
    """The trap. roleInChain is COMMON on all fourteen slots of every dual,
    because it describes branches WITHIN a path and a dual has none. Reading
    the role field alone would make a dual look like a straight path."""
    for dual in (t for t in table if t.kind is T.TopologyKind.DUAL):
        assert set(dual.roles) == {T.SlotRole.COMMON}, dual.name
        assert len(dual.paths) == 2, dual.name
        assert sum(len(p) for p in dual.paths) == T.SLOTS
        # every slot exactly once, and the two paths disjoint: a sum of 14
        # alone would accept an 8 and a 6 that shared slots
        assert sorted(dual.paths[0] + dual.paths[1]) == list(range(1, T.SLOTS + 1))


def test_every_dual_partition_is_pinned_exactly(table):
    """All four, including index 9, whose name states no numbers at all and
    whose split is therefore measured off geometry rather than read off the
    name. Leaving it out was how an unlabelled 14 // 2 constant nearly
    shipped."""
    assert table.get(6).paths == (tuple(range(1, 8)), tuple(range(8, 15)))
    assert table.get(7).paths == (tuple(range(1, 5)), tuple(range(5, 15)))
    assert table.get(8).paths == (tuple(range(1, 3)), tuple(range(3, 15)))
    assert table.get(9).paths == (tuple(range(1, 8)), tuple(range(8, 15)))


def test_role_and_path_answer_different_questions(table):
    """A dual reports COMMON everywhere AND two paths. Code that asked only
    `role` would conclude slot 3 and slot 9 are on the same run."""
    dual = table.get(7)
    assert dual.role(3) is dual.role(9) is T.SlotRole.COMMON
    assert dual.path_of(3) != dual.path_of(9)


# --- what it refuses to claim ------------------------------------------

def test_separate_paths_never_feed_each_other(table):
    assert table.get(7).feeds(3, 9) is False


def test_parallel_branches_never_feed_each_other(table):
    sps1 = table.get(1)
    assert sps1.feeds(4, 8) is False
    assert sps1.feeds(8, 4) is False


def test_the_same_lane_is_ordered_by_slot(table):
    sps1 = table.get(1)
    assert sps1.feeds(4, 5) is True
    assert sps1.feeds(5, 4) is False
    assert sps1.feeds(1, 2) is True


def test_crossing_the_split_is_derivable_and_is_answered(table):
    """An earlier version returned None here while returning True for
    common-to-common across the same split. That was a contradiction, not
    caution: both rest on the identical fact, that the split falls between the
    last common slot and the first branch slot, and the role runs give it."""
    sps1 = table.get(1)
    assert sps1.feeds(1, 4) is True        # common -> branch A
    assert sps1.feeds(4, 12) is True       # branch A -> common
    assert sps1.feeds(1, 12) is True       # and across the whole split
    assert sps1.feeds(12, 4) is False      # but not backwards


def test_no_routing_on_this_firmware_answers_unknown(table):
    """None is reserved for a run order this model has not seen, and none of
    the ten is one. If a firmware adds a shape we cannot read, feeds() says so
    rather than forcing it into the nearest pattern."""
    for top in table:
        assert top.modelled, top.name
        for a in range(1, T.SLOTS + 1):
            for b in range(1, T.SLOTS + 1):
                assert top.feeds(a, b) is not None


def test_an_unreadable_run_order_is_marked_rather_than_forced():
    """The escape hatch, exercised. A branch order this model has not seen
    must not be silently read as a middle split."""
    weird = (T.SlotRole.BRANCH_A, T.SlotRole.COMMON, T.SlotRole.BRANCH_B)
    stages, modelled = T._stages(weird)
    assert modelled is False
    assert stages == (0, 0, 0)


def test_stages_run_pre_branch_post(table):
    assert table.get(1).stages == (0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 2, 2, 2)
    assert table.get(4).stages == (1,) * 10 + (2,) * 4   # Immediate Split
    assert set(table.get(0).stages) == {0}               # Straight Path


def test_an_unknown_routing_or_slot_raises(table):
    with pytest.raises(T.UnknownTopology):
        table.get(10)
    with pytest.raises(T.UnknownTopology):
        table.get(0).role(15)
    with pytest.raises(T.UnknownTopology):
        table.get(0).role(0)


# --- the picker hazard -------------------------------------------------

def test_display_order_is_not_index_order(table):
    """Measured: index 5 is drawn sixth and index 9 fifth. Anything writing
    Routing from a menu position selects the wrong topology."""
    assert table.get(5).display_order == 6
    assert table.get(9).display_order == 5
    assert [t.index for t in table.in_display_order()] != list(range(10))


def test_names_are_returned_in_index_order(table):
    """Because that is the order Chain.Routing takes."""
    names = table.names()
    assert names[0] == "S"
    assert names[5] == "Vocal"
    assert names[9] == "DualGuit"


def test_schema_names_match_the_device_enumeration(table):
    """The committed schema is the authority on what the ten are called; the
    table must not drift from it."""
    schema = json.loads((T.CONFIG / "headrush_schema.json").read_text(encoding="utf-8"))
    meta = schema["metas"][schema["paths"]["/Evil/Engine/Patch/Chain"]]
    published = meta["properties"]["Routing"]["x-options"]["strings"]
    assert table.names() == published


def test_a_topology_cannot_be_edited(table):
    """On a SELECTED device you choose a topology; you do not build one.

    Typed: a bare `raises(Exception)` would pass on a misspelled attribute.
    """
    with pytest.raises(dataclasses.FrozenInstanceError):
        table.get(0).index = 3


def test_provenance_travels_with_each_topology(table):
    """The table carried it and the individual Topology did not, so anything
    holding one had device-shaped answers with nothing to cite. `sim.topology`
    hands out exactly this object."""
    for top in table:
        assert top.api_readable is False
        assert top.provenance == "vendor editor bundle"
