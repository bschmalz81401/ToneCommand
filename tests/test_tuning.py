"""#138: the tuning parser is deterministic, boundary-safe and speaks IRCommand's vocabulary."""
import pytest

from fm9.tuning import parse_tuning

TABLE = [
    ("we play in drop C", "drop c"),
    ("Drop-C rhythm tone", "drop c"),
    ("drop C# for this song", "drop c"),
    ("tuned to drop Db", "drop c"),
    ("drop c sharp chug", "drop c"),
    ("drop B seven string feel", "drop b"),
    ("drop A# djent", "drop b"),
    ("drop A, very low", "drop a"),
    ("drop d punk", "drop d"),
    ("dropped D", "drop d"),
    ("Eb standard like Van Halen", "eb"),
    ("half step down", "eb"),
    ("a half-step down tuning", "eb"),
    ("E flat tuning, bluesy", "eb"),
    ("e-flat", "eb"),
    ("tuned to Eb", "eb"),
    ("D standard doom", "d standard"),
    ("a whole step down", "d standard"),
    ("C standard sludge", "c standard"),
    ("C# standard", "c standard"),
    ("Db standard", "c standard"),
    ("B standard", "b standard"),
    ("Bb standard", "b standard"),
    ("standard tuning, bright clean", None),
    ("E standard lead", None),
    ("a big lead tone", None),
    ("", None),
    (None, None),
]


@pytest.mark.parametrize("text,expected", TABLE)
def test_phrasings(text, expected):
    assert parse_tuning(text) == expected


def test_boundaries_never_match_inside_other_words():
    assert parse_tuning("raindrop delay, standardised") is None
    assert parse_tuning("the backdrop demands a dark tone") is None
    assert parse_tuning("dropdead gorgeous cleans") is None
    assert parse_tuning("web standard") is None  # 'eb' inside 'web'
    assert parse_tuning("celeb tone") is None


def test_the_most_specific_phrase_wins():
    assert parse_tuning("drop c# not drop c") == "drop c"  # both mean drop c on the wire
    assert parse_tuning("c# standard rather than c standard") == "c standard"
    assert parse_tuning("drop a# on the seven") == "drop b"


def test_vocabulary_matches_ircommands_tuning_axes():
    from fm9.tuning import TUNINGS
    assert [v for v, _ in TUNINGS] == ["drop c", "drop b", "drop a", "drop d", "c standard", "b standard", "d standard", "eb"]
