"""Self-checks for badge-level detection and the classification ladder.

Run: cd app && PYTHONPATH=.:.. python routers/test_qualifications.py
"""
from core.qualifications import (
    BADGE_TYPE_BY_KEY, CLASSIFICATION_ORDER,
    classification_rank, held_level, held_levels,
)

LEADERSHIP = BADGE_TYPE_BY_KEY["leadership"]
MUSIC = BADGE_TYPE_BY_KEY["music"]
MOI = BADGE_TYPE_BY_KEY["moi"]


def test_held_levels():
    # Every matching rung comes back, highest first.
    assert held_levels(LEADERSHIP, ["Blue Leadership", "Silver Leadership"]) == ("silver", "blue")
    assert held_levels(LEADERSHIP, []) == ()

    # Both naming variants of the same rung count, and only once.
    assert held_levels(LEADERSHIP, ["Bronze Air Cadet Foundation Leadership"]) == ("bronze",)

    # A cadet who jumped a rung has no record of the one below — the badge-order
    # check relies on this to tell "holds a higher level" from "holds it".
    assert held_levels(LEADERSHIP, ["Gold Leadership"]) == ("gold",)

    # "Musician" is a substring of the higher tiers, so a single Wing Musician
    # qual must not read as blue *and* bronze.
    assert held_levels(MUSIC, ["Wing Musician (Bronze) - Drums"]) == ("bronze", "blue")

    # Boolean badges have the one rung.
    assert held_levels(MOI, ["Instructor Cadet"]) == ("yes",)
    assert held_levels(MOI, ["Methods of Instruction(Training)"]) == ()

    print("held_levels self-check passed")


def test_held_level_is_the_highest():
    assert held_level(LEADERSHIP, ["Blue Leadership", "Silver Leadership"]) == "silver"
    assert held_level(LEADERSHIP, ["blue leadership"]) == "blue"  # matching is case-insensitive
    assert held_level(LEADERSHIP, ["Bronze Road Marching"]) is None
    assert held_level(LEADERSHIP, []) is None
    print("held_level self-check passed")


def test_classification_rank():
    assert classification_rank("Master Air Cadet") > classification_rank("Leading Cadet")
    assert classification_rank(CLASSIFICATION_ORDER[0]) == 0

    # Anything off the ladder — no classification recorded, or the scraper's
    # "Junior Cadet" placeholder — ranks below every rung.
    assert classification_rank(None) == -1
    assert classification_rank("Junior Cadet") == -1
    assert classification_rank("Second Class Cadet") > classification_rank(None)
    print("classification_rank self-check passed")


if __name__ == "__main__":
    test_held_levels()
    test_held_level_is_the_highest()
    test_classification_rank()
    print("All qualification self-checks passed")
