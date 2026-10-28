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


# ── weapon handling tests ─────────────────────────────────────────────────────

def _wht(weapon, passed, expires, by):
    # What _read_wht_rows returns for one row of Bader's WHTGridView.
    return {"lblWHTType": weapon, "lblWHTDate": passed, "lblWHTDateExpires": expires, "lblAwardedBy": by}


def test_wht_rows_are_parsed_from_bader_grid_labels():
    from datetime import date
    rows = [
        _wht("Scorpion (single shot)", "10/08/2021", "09/02/2022", "WO K. Duffy (AC/07333)"),
        _wht("Scorpion (single shot)", "02/08/2024", "02/02/2025", "Doherty, S Flt Lt"),
        _wht("L98A2", "28/03/2025", "", "  "),
        # The same row twice (a redraw) is one test.
        _wht("L98A2", "28/03/2025", "", ""),
        # No weapon or no pass date isn't a test.
        _wht("", "28/03/2025", "28/09/2025", ""),
        _wht("L98A2", "", "28/09/2025", ""),
    ]
    assert qs._parse_wht_rows(rows) == [
        {"weapon": "Scorpion (single shot)", "date_passed": date(2021, 8, 10),
         "date_expires": date(2022, 2, 9), "awarded_by": "WO K. Duffy (AC/07333)"},
        {"weapon": "Scorpion (single shot)", "date_passed": date(2024, 8, 2),
         "date_expires": date(2025, 2, 2), "awarded_by": "Doherty, S Flt Lt"},
        {"weapon": "L98A2", "date_passed": date(2025, 3, 28), "date_expires": None, "awarded_by": None},
    ]


def test_no_wht_rows_parse_to_an_empty_list():
    assert qs._parse_wht_rows([]) == []


def test_a_wht_tab_that_never_loads_reads_as_none_not_empty(monkeypatch):
    # None makes the save keep the stored tests; [] would look like "has none".
    class NoTab:
        def wait_for_selector(self, selector, timeout):
            raise PlaywrightTimeoutError("no tab")

    assert qs.get_whts(NoTab()) is None


def _wht_page(monkeypatch, first_read, full_read=None):
    """get_whts on a fake page whose grid reads ``first_read`` rows, and
    ``full_read`` once every row is shown. Records whether that was asked for."""
    calls = {"expanded": False}
    reads = iter([first_read, full_read])

    class Page:
        def wait_for_selector(self, selector, timeout):
            pass

    def expand(page, select_name, table_id):
        assert select_name == f"{qs.WHT_GRID}_length"
        calls["expanded"] = True

    monkeypatch.setattr(qs, "open_tab", lambda page, dropdown, item: None)
    monkeypatch.setattr(qs, "_read_wht_rows", lambda page: next(reads))
    monkeypatch.setattr(qs, "ensure_all_rows_shown", expand)
    return Page(), calls


def test_a_short_wht_grid_is_read_as_is(monkeypatch):
    # Fewer rows than a page has no length control; waiting for one cost 20s a cadet.
    page, calls = _wht_page(monkeypatch, [_wht("L98A2", "28/03/2025", "28/09/2025", "")] * 2)
    assert len(qs.get_whts(page)) == 1 and calls["expanded"] is False


def test_an_empty_wht_grid_is_an_empty_list(monkeypatch):
    page, calls = _wht_page(monkeypatch, [])
    assert qs.get_whts(page) == [] and calls["expanded"] is False


def test_a_full_first_page_of_whts_shows_every_row_before_reading(monkeypatch):
    page_one = [_wht("L98A2", f"{d:02d}/01/2025", "", "") for d in range(1, 11)]
    every_row = page_one + [_wht("L98A2", "11/01/2025", "", "")]
    page, calls = _wht_page(monkeypatch, page_one, every_row)
    assert len(qs.get_whts(page)) == 11 and calls["expanded"] is True
