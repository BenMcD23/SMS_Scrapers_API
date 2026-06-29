"""Self-checks for staff scraper logic (run: python -m scripts.test_match_email)."""
from datetime import date

from scripts.scraper_utils import match_email
from scripts.staff_scraper import attendance_periods

EMAILS = {
    ("JONATHON", "BARKER"): "j.barker@x",
    ("JACOB", "GILL"): "jacob.gill@x",
    ("JOSEPH", "GILL"): "joseph.gill@x",
    ("ROY", "CATTERALL"): "r.catterall@x",
}


def test():
    # exact
    assert match_email("JONATHON", "BARKER", EMAILS) == "j.barker@x"
    # first-initial + surname (two Gills -> J... matches Jacob via first hit)
    assert match_email("JAKE", "GILL", EMAILS) == "jacob.gill@x"
    # sole surname match when first name differs entirely
    assert match_email("BOB", "CATTERALL", EMAILS) == "r.catterall@x"
    # no match -> None (the "pass if can't find an email" case)
    assert match_email("ALAN", "SMITH", EMAILS) is None
    # ambiguous surname, no initial match -> None (two Gills, neither starts with X)
    assert match_email("XAVIER", "GILL", EMAILS) is None
    print("match_email self-check passed")


def test_attendance_periods():
    # January: previous year Jul–Dec, then current January
    assert attendance_periods(date(2026, 1, 15)) == \
        [(2025, m) for m in range(7, 13)] + [(2026, 1)]
    # March (still H1): previous Jul–Dec + Jan..Mar
    assert attendance_periods(date(2026, 3, 1)) == \
        [(2025, m) for m in range(7, 13)] + [(2026, 1), (2026, 2), (2026, 3)]
    # September (H2): current year only, no previous year
    assert attendance_periods(date(2026, 9, 1)) == [(2026, m) for m in range(1, 10)]
    # June boundary still counts as H1
    assert (2025, 7) in attendance_periods(date(2026, 6, 30))
    # July boundary is H2 — no previous year
    assert all(y == 2026 for y, _ in attendance_periods(date(2026, 7, 1)))
    print("attendance_periods self-check passed")


def test_compute_htd():
    from form_generators.HTD_gen import compute_htd

    c = compute_htd(10, [4, 3, 0, 0, 0, 0])
    assert c["car_cost"] == 2.68, c         # 10 * 0.25 * 1.07 = 2.675 -> 2.68
    assert c["total_a"] == c["car_cost"], c  # 7% applied at car_cost, carried down
    assert c["amounts"] == [10.72, 8.04, 0.0, 0.0, 0.0, 0.0], c
    assert c["totals"] == c["amounts"], c
    assert c["total_claimed"] == 18.76, c
    # zero distance -> all zero
    z = compute_htd(0, [5])
    assert z["total_a"] == 0.0 and z["total_claimed"] == 0.0, z
    print("compute_htd self-check passed")


if __name__ == "__main__":
    test()
    test_attendance_periods()
    test_compute_htd()
