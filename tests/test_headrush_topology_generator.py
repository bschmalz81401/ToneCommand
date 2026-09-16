"""The topology generator, as a pure function on synthetic bundles.

#33 phase 3, #121. `tests/test_headrush_topology.py` checks the committed
artifact; this checks the code that produces it, which nothing covered. The
schema generator from #117 is tested the same way and for the same reason: a
parser only ever exercised on the one input it was written against is a parser
whose failure modes are unknown.

No unit, no network, no committed file.
"""
import pytest

from tools import build_headrush_topologies as gen


def _routing(index, name, positions, roles, *, inputs=1,
             display=None, vocals=False):
    """A routing as `parse()` would have produced it."""
    return {
        "index": index,
        "name": name,
        "display_order": index if display is None else display,
        "requires_vocals": vocals,
        "io_blocks": ["Input", "Output"] * inputs,
        "input_types": ["guitar"] * inputs,
        "slots": [{"slot": n, "position": list(p), "role": r}
                  for n, (p, r) in enumerate(zip(positions, roles), start=1)],
    }


def _row(n, y=93, x0=100, pitch=76):
    return [(x0 + i * pitch, y) for i in range(n)]


# --- parse() ------------------------------------------------------------

def test_parse_reads_a_minified_definition():
    bundle = (
        'junk{index:0,name:"Straight Path",displayOrder:0,requiresVocals:!1,'
        'layout:x,ioBlockName:"Input",inputType:"guitar",ioBlockName:"Output",'
        'slots:[{position:[167,93],roleInChain:0},'
        '{position:[243,93],roleInChain:1}]}tail'
    )
    got = gen.parse(bundle)
    assert len(got) == 1
    r = got[0]
    assert r["index"] == 0 and r["name"] == "Straight Path"
    assert r["display_order"] == 0
    assert r["requires_vocals"] is False
    assert r["io_blocks"] == ["Input", "Output"]
    assert r["slots"] == [
        {"slot": 1, "position": [167, 93], "role": 0},
        {"slot": 2, "position": [243, 93], "role": 1},
    ]


def test_parse_reads_requires_vocals_from_the_minifiers_bang_notation():
    """`requiresVocals:!0` is true and `!1` is false. Getting this backwards
    would mislabel which routings are vocal, silently."""
    true_ = gen.parse('{index:0,name:"V",requiresVocals:!0,slots:[]}')
    false_ = gen.parse('{index:0,name:"G",requiresVocals:!1,slots:[]}')
    assert true_[0]["requires_vocals"] is True
    assert false_[0]["requires_vocals"] is False


def test_parse_splits_adjacent_definitions_rather_than_merging_them():
    bundle = ('{index:0,name:"A",slots:[{position:[1,1],roleInChain:0}]}'
              '{index:1,name:"B",slots:[{position:[2,2],roleInChain:1}]}')
    got = gen.parse(bundle)
    assert [r["name"] for r in got] == ["A", "B"]
    assert [len(r["slots"]) for r in got] == [1, 1]


# --- paths_of() ---------------------------------------------------------

def test_a_single_input_routing_is_one_path():
    r = _routing(0, "Straight Path", _row(14), [0] * 14, inputs=1)
    paths, how = gen.paths_of(r)
    assert paths == [list(range(1, 15))]
    assert how == "single path"


def test_side_by_side_paths_are_split_on_the_x_gap():
    """Dual Path 4-10: two slots left, ten right, with a wide margin."""
    positions = [(120, 93), (196, 93), (196, 250), (120, 250)] + \
                [(x, y) for y in (93, 250) for x, _ in _row(5, x0=400)]
    r = _routing(7, "Dual Path 4-10", positions, [0] * 14, inputs=2)
    paths, how = gen.paths_of(r)
    assert paths == [[1, 2, 3, 4], list(range(5, 15))]
    assert how.startswith("x geometry")
    assert "agrees with the name" in how


def test_stacked_paths_are_split_on_y_when_x_cannot_separate_them():
    """Dual Straight Path: both paths share every x, so only y carries it.

    This is the case that nearly shipped as `SLOTS // 2`. The name states no
    numbers, so there is nothing to read it off except the geometry.
    """
    positions = _row(7, y=93) + _row(7, y=250)
    r = _routing(9, "Dual Straight Path", positions, [0] * 14, inputs=2)
    paths, how = gen.paths_of(r)
    assert paths == [list(range(1, 8)), list(range(8, 15))]
    assert how == "y geometry"


def test_a_wrapped_single_path_is_not_mistaken_for_two():
    """The trap in the y rule. A long single path is drawn as two rows too,
    and it must stay one path: the IO check is what keeps it out."""
    positions = _row(7, y=93) + _row(7, y=250)
    r = _routing(0, "Straight Path", positions, [0] * 14, inputs=1)
    paths, _ = gen.paths_of(r)
    assert paths == [list(range(1, 15))]


def test_geometry_disagreeing_with_the_name_refuses_rather_than_picks():
    """The name is a cross-check, not the source. If the two disagree one of
    them is wrong and choosing silently would bake in whichever was."""
    positions = [(120, 93), (196, 93)] + \
                [(x, 93) for x, _ in _row(12, x0=400)]
    r = _routing(7, "Dual Path 4-10", positions, [0] * 14, inputs=2)
    with pytest.raises(SystemExit, match="geometry splits 2/12"):
        gen.paths_of(r)


def test_a_dual_whose_slots_do_not_separate_refuses():
    """No axis carries a gap and there is nothing to measure. A guess here
    would be a partition presented as a measurement."""
    positions = _row(14)                      # one row, even pitch
    r = _routing(6, "Dual Path 7-7", positions, [0] * 14, inputs=2)
    with pytest.raises(SystemExit, match="will not be guessed"):
        gen.paths_of(r)


# --- build() refuses rather than shipping something short ---------------

def test_fewer_than_ten_definitions_is_a_parser_problem_not_a_smaller_file():
    bundle = '{index:0,name:"Straight Path",slots:[]}'
    with pytest.raises(SystemExit, match="expected 10"):
        gen.build(bundle, "main.js", None)


def test_a_routing_with_the_wrong_slot_count_refuses():
    defs = "".join(
        '{index:%d,name:"R%d",displayOrder:%d,requiresVocals:!1,slots:[%s]}'
        % (i, i, i, ",".join('{position:[%d,93],roleInChain:0}' % (100 + 76 * n)
                             for n in range(14 if i else 13)))
        for i in range(10))
    with pytest.raises(SystemExit, match="expected 14"):
        gen.build(defs, "main.js", None)
