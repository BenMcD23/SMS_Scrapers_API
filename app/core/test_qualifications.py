"""held_level's substring vs exact-name matching, using the ATP Ground School
badge (exact) alongside an existing substring badge."""

from core.qualifications import BADGE_TYPE_BY_KEY, BADGE_TYPES, held_level

ATP = BADGE_TYPE_BY_KEY["atp_ground_school"]


def test_atp_matches_exact_names():
    assert held_level(ATP, ["Blue ATP Ground School"]) == "blue"
    assert held_level(ATP, ["Bronze ATP Ground School"]) == "bronze"
    # Highest level wins when both are held.
    assert held_level(ATP, ["Blue ATP Ground School", "Bronze ATP Ground School"]) == "bronze"


def test_atp_is_case_and_whitespace_insensitive():
    assert held_level(ATP, ["  blue atp ground school "]) == "blue"


def test_atp_rejects_names_that_only_contain_the_pattern():
    assert held_level(ATP, ["Blue ATP Ground School (Instructor)"]) is None
    assert held_level(ATP, ["Pre Bronze ATP Ground School"]) is None
    assert held_level(ATP, []) is None


def test_substring_badges_still_match_variants():
    shooting = BADGE_TYPE_BY_KEY["shooting"]
    assert held_level(shooting, ["Silver Shot (Air Rifle)"]) == "silver"


def test_atp_listed_directly_before_flying():
    keys = [b.key for b in BADGE_TYPES]
    assert keys.index("atp_ground_school") == keys.index("flying") - 1
