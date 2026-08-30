"""An audit has to show the confidence of its own verdict.

path_audit answers "can this scene make sound". Its walk assumes that a block
it cannot identify passes signal, which is right for almost every block and is
still an assumption. Reported as a bare "alive", a path leaning on one reads
exactly like a path that leans on nothing.

Found while diagnosing a silent preset: 510 has two engaged blocks (effect ids
200 and 201) with no registry entry, and the audit had called every scene alive
without mentioning them. They turned out not to be on the grid at all, so that
verdict was clean, but nothing in the output said so either way.
"""
from fm9.registry import Registry
from fm9.sim import SimFM9
from tools.path_audit import scene_alive, walk


def _state(dev):
    return {x.effect_id: x for x in dev.status_dump() or []}


def test_a_clean_path_says_nothing_extra():
    reg = Registry()
    dev = SimFM9(reg)
    with dev:
        ok, why = scene_alive(dev.read_grid(), _state(dev), reg)
    assert ok and why == "alive"


def test_an_unidentified_block_on_the_path_is_named():
    """Not folded into the verdict, and not silently trusted."""
    reg = Registry()
    dev = SimFM9(reg)
    with dev:
        cells = dev.read_grid() or []
        st = _state(dev)
        # a block the registry has no entry for, standing where the amp stands
        amp = reg.effect_id("DISTORT")
        for c in cells:
            if c.effect_id == amp:
                c.effect_id = 999
        for x in list(st.values()):
            if x.effect_id == amp:
                x.effect_id = 999
                st[999] = x
        ok, why = scene_alive(cells, st, reg)
    assert ok, "an unknown block is still assumed to pass; that is the point"
    assert "ASSUMING" in why and "999" in why
    assert "no entry" in why


def test_only_unknowns_on_the_live_path_count():
    """One sitting in a dead branch says nothing about whether this scene
    makes sound, and naming it would be noise that trains people to skim."""
    reg = Registry()
    dev = SimFM9(reg)
    with dev:
        cells = dev.read_grid() or []
        st = _state(dev)
        w = walk(cells, st, reg)
        assert w["assumed"] == []
        # an unknown block in the status dump but not on the grid, exactly the
        # case observed on 510 with effect ids 200 and 201
        class Ghost:
            effect_id, bypassed, channel, channels_supported = 200, False, 0, 1
        st[200] = Ghost()
        ok, why = scene_alive(cells, st, reg)
    assert ok and why == "alive", why


def test_the_reason_reaches_every_caller():
    """Folded into `why` rather than added as a return value, so the printed
    report, the JSON and the health scan all inherit it without changing a
    signature any of them depend on."""
    import inspect
    src = inspect.getsource(scene_alive)
    assert "return w[\"alive\"], why" in src
    assert len(inspect.signature(scene_alive).parameters) == 3
