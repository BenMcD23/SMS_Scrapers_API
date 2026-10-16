"""Opening a cadet's profile in the quali scraper, with Playwright faked out."""

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import scripts.quali_scraper as qs


class FakePage:
    """A page whose CIN label shows up only on the given open attempts."""

    def __init__(self, cin_on_attempts):
        self.cin_on_attempts, self.opens = cin_on_attempts, 0

    def wait_for_selector(self, selector, timeout):
        assert selector == qs.CIN_LABEL
        if self.opens not in self.cin_on_attempts:
            raise PlaywrightTimeoutError("no CIN")


def _patch_open(monkeypatch, fast=False):
    def fake_open(page, i, links, kind, n):
        page.opens += 1
        return fast
    monkeypatch.setattr(qs, "open_profile", fake_open)


def test_a_profile_that_shows_its_cin_is_opened_once(monkeypatch):
    _patch_open(monkeypatch, fast=True)
    page = FakePage({1})
    assert qs._open_cadet_profile(page, 0, {}, 5) is True
    assert page.opens == 1


def test_a_profile_still_on_the_list_page_is_reopened(monkeypatch):
    # The click fallback can return before navigating; the first read then sees
    # the list page, which has no CIN.
    _patch_open(monkeypatch)
    page = FakePage({2})
    qs._open_cadet_profile(page, 0, {}, 5)
    assert page.opens == 2


def test_a_profile_that_never_shows_a_cin_gives_up_after_two_opens(monkeypatch):
    _patch_open(monkeypatch)
    page = FakePage(set())
    qs._open_cadet_profile(page, 0, {}, 5)  # no raise: the save step skips it safely
    assert page.opens == 2
