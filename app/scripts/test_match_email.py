"""Self-check for match_email (run: python -m scripts.test_match_email)."""
from scripts.scraper_utils import match_email

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


if __name__ == "__main__":
    test()
