"""The events page filters end up as we want them, whatever the account had
saved. Runs the real script in an offline Chromium page; skipped where no
browser is installed (CI)."""

import pytest

from scripts.event_scraper import EVENT_FILTERS, _set_filters

sync_api = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def page():
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:
            pytest.skip("no Chromium installed")
        yield browser.new_page()
        browser.close()


def _boxes(saved: dict) -> str:
    return "".join(
        f'<input type="checkbox" name="ctl00$ctl00$cphBaseBody$cphBody${n}" {"checked" if on else ""}>'
        for n, on in saved.items()
    )


def _states(page) -> dict:
    return {n: page.is_checked(f"[name='ctl00$ctl00$cphBaseBody$cphBody${n}']") for n in EVENT_FILTERS}


@pytest.mark.parametrize("saved", [
    {"cbAdultIC": True, "cbMyUnit": False, "cbAttending": False},   # the squadron account's defaults
    {"cbAdultIC": False, "cbMyUnit": True, "cbAttending": True},    # a personal account already filtered
    {"cbAdultIC": False, "cbMyUnit": False, "cbAttending": False},
    {"cbAdultIC": True, "cbMyUnit": True, "cbAttending": True},
])
def test_filters_are_set_not_toggled(page, saved):
    # A blind click per box only worked for the first case; the second came
    # out as "Adult IC only" and found none of the user's events.
    page.set_content(_boxes(saved))
    _set_filters(page)
    assert _states(page) == EVENT_FILTERS


def test_a_missing_filter_box_is_left_alone(page):
    page.set_content(_boxes({"cbMyUnit": False, "cbAttending": False}))
    _set_filters(page)
    assert page.is_checked("[name='ctl00$ctl00$cphBaseBody$cphBody$cbMyUnit']")
