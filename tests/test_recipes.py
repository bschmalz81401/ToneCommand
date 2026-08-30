"""Finding and using other people's tones, without needing a GitHub account.

Sharing was wired to open a prefilled GitHub ISSUE, which was wrong three ways
at once: an issue is not a container for a recipe, the tracker would silt up
with them, and it asked a guitarist to learn a developer's tool before they
could give anything back.

The fix was to stop conflating the two halves. Consuming is the ninety-five
percent case and needs no account at all. Contributing genuinely needs
somewhere to put a file, and there are two honest paths rather than one that
fits everyone.
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server
from fm9 import recipes

UI = (Path(__file__).resolve().parent.parent / "ui" / "index.html").read_text()
SCRIPT = UI.split("<script>")[1]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("TONECOMMAND_RECIPES_DIR", str(tmp_path / "recipes"))
    monkeypatch.setattr(recipes, "_cache", {"at": 0.0, "items": None})


@pytest.fixture
def client():
    return TestClient(server.app)


RECIPE = {"recipe_version": 1, "name": "test-tone", "title": "A test tone",
          "device": "FM9", "author": "someone",
          "steps": [{"kind": "set_param", "block": "DISTORT", "instance": 1,
                     "param": "DISTORT_MID", "value": 6.0, "reason": "x"}]}


# --- the tracker is not a container for recipes --------------------------

def test_sharing_no_longer_opens_an_issue():
    """An issue is not a recipe, and a few dozen of these would bury the
    actual bugs."""
    assert "issues/new" not in SCRIPT
    fn = SCRIPT.split("async function shareDesign")[1].split("\n}\n")[0]
    assert "/api/recipes/save" in fn


def test_contributing_targets_the_recipes_folder_not_the_tracker():
    """GitHub's new-file page takes a path and content in the query and offers
    "Propose new file", which forks and opens the pull request without the
    contributor touching git."""
    url = recipes.pr_url(RECIPE)
    assert "/new/" in url and "filename=recipes/test-tone.json" in url
    assert "issues" not in url


def test_a_recipe_name_cannot_escape_the_folder():
    assert recipes._safe_name("../../etc/passwd") == "etc-passwd"
    assert recipes._safe_name("") == "untitled"
    assert "/" not in recipes._safe_name("a/b/c")


# --- reading needs no account -------------------------------------------

def test_the_catalogue_reads_local_and_shared_together(client, monkeypatch):
    recipes.save_local(RECIPE)
    monkeypatch.setattr(recipes, "fetch_shared",
                        lambda timeout=6.0: ([{"name": "theirs", "title": "T",
                                               "_source": "shared",
                                               "_file": "theirs.json"}], None))
    d = client.get("/api/recipes").json()
    names = {r["name"] for r in d["recipes"]}
    assert names == {"test-tone", "theirs"}
    assert {r["_source"] for r in d["recipes"]} == {"local", "shared"}


def test_your_own_copy_wins_over_the_shared_one(client, monkeypatch):
    """Otherwise editing a recipe you pulled down would show you two of it."""
    recipes.save_local(RECIPE)
    monkeypatch.setattr(recipes, "fetch_shared",
                        lambda timeout=6.0: ([{"name": "test-tone",
                                               "_source": "shared",
                                               "_file": "test-tone.json"}], None))
    d = client.get("/api/recipes").json()
    assert len(d["recipes"]) == 1 and d["recipes"][0]["_source"] == "local"


def test_an_unreachable_catalogue_is_reported_not_shown_as_empty(client, monkeypatch):
    """An empty list would read as "nobody has shared anything", which is a
    very different and much more discouraging statement than "offline"."""
    monkeypatch.setattr(recipes, "fetch_shared",
                        lambda timeout=6.0: ([], "could not reach the shared recipes"))
    d = client.get("/api/recipes").json()
    assert d["shared_error"]
    assert "shared_error" in SCRIPT or "shared recipes unavailable" in SCRIPT


# --- a recipe is validated against YOUR device before it runs ------------

def test_using_a_recipe_validates_every_step_first(client):
    """This is what makes a recipe portable rather than a preset file with
    extra steps: a step naming a block you do not have is reported here
    rather than failing on the wire."""
    d = client.post("/api/recipes/plan", json={"recipe": RECIPE}).json()
    assert len(d["actions"]) == 1
    assert "validation_errors" in d["actions"][0]
    assert d["blocked"] == 0


def test_a_step_this_device_cannot_run_is_marked_not_dropped(client):
    bad = {**RECIPE, "steps": [{"kind": "set_param", "block": "DISTORT",
                                "instance": 1, "param": "NOPE", "value": 1}]}
    d = client.post("/api/recipes/plan", json={"recipe": bad}).json()
    assert d["blocked"] == 1
    assert d["actions"][0]["validation_errors"]


def test_both_spellings_of_the_step_list_are_read():
    """docs/RECIPES.md writes them as `actions`; the exporter wrote `steps`.
    Reading both is one line and saves every recipe written either way."""
    assert len(recipes.steps_of({"steps": [1, 2]})) == 2
    assert len(recipes.steps_of({"actions": [1, 2, 3]})) == 3
    assert recipes.steps_of({}) == []


def test_using_a_recipe_goes_through_the_confirm_gate():
    fn = SCRIPT.split("async function useRecipe")[1].split("\n}\n")[0]
    assert "showPlan(" in fn and "/api/apply" not in fn


def test_the_shipped_recipes_all_still_parse():
    """They are the catalogue: one that will not load is a broken shelf."""
    d = Path(__file__).resolve().parent.parent / "recipes"
    files = [f for f in d.glob("*.json") if f.name != "index.json"]
    assert files, "no recipes shipped"
    for f in files:
        rec = json.loads(f.read_text())
        assert rec.get("recipe_version") == 1, f.name
        assert recipes.steps_of(rec), f"{f.name} has no steps"
