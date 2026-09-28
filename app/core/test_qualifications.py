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


def test_order_badge_held():
    from core.qualifications import order_badge_held as held
    # Exact level only; a higher level doesn't count.
    assert held("Shooting – Bronze", ["Bronze Shot (L98A2)"], None) is True
    assert held("Shooting – Bronze", ["Gold Shot (Air Rifle)"], None) is False
    assert held("Space – Blue", ["OU Applications of Space Technology (Blue)"], None) is True
    assert held("Radio – Silver", ["Radio - Advanced Voice Procedure (Silver)"], None) is True
    assert held("Road Marching – Gold (Nijmegen)", ["Nijmegen Road Marching"], None) is True
    # Flying Blue/Bronze need ATP Ground School too; Silver doesn't.
    atp_blue = "RAFAC Aviation Training Package Blue Training Badge"
    assert held("Flying – Blue", [atp_blue], None) is False
    assert held("Flying – Blue", [atp_blue, "Blue ATP Ground School"], None) is True
    assert held("Flying – Silver", ["RAFAC Silver Flying Badge"], None) is True
    # Classification comes off the cadet, exact only.
    assert held("Senior", [], "Senior Cadet") is True
    assert held("Senior", [], "Master Air Cadet") is False
    assert held("ATC", ["anything"], None) is None
