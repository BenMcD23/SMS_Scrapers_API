"""Reading Bader's Next Of Kin mail-merge report, with Playwright faked out.

Rows are given as the cell texts read_report_cells returns: a spacer cell, then
Personnel Type, Service Number, Forename, Surname, DOB, Relationship, Email.
"""

from datetime import datetime
from types import SimpleNamespace

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import scripts.parent_email_scraper as nok
import scripts.reports as reports

HEADINGS = ["", "Personnel Type", "Service Number", "Forename", "Surname", "Date of Birth", "Relationship", "Email"]


def heading(kind):
    # Bader renders the empty cells as &nbsp;, which innerText gives back as \xa0.
    return ["", kind] + ["\xa0"] * 6


def person(first, last, email, dob="9/29/2011", relationship="Mother", service="3103170024018"):
    return ["", "\xa0", service, first, last, dob, relationship, email]


# ── parsing ───────────────────────────────────────────────────────────────────

def test_only_rows_under_the_cdt_heading_are_kept():
    rows = [
        HEADINGS,
        heading("CDT"),
        person("Aiman", "Shahbaz", "shahbaz163@yahoo.com", relationship="Father"),
        person("Alice", "Davies", "jaderusselljrr@gmail.com", dob="5/8/2008"),
        heading("CI"),
        person("Ivor", "Instructor", "ci.parent@example.com"),
    ]
    records, current = nok._parse_nok_rows(rows)
    assert [(r["first_name"], r["email"], r["relationship"]) for r in records] == [
        ("Aiman", "shahbaz163@yahoo.com", "Father"),
        ("Alice", "jaderusselljrr@gmail.com", "Mother"),
    ]
    assert current == "CI"


def test_the_date_of_birth_is_month_first():
    """The report is en-US: 5/8/2008 is the 8th of May, not the 5th of August."""
    [r] = nok._parse_nok_rows([heading("CDT"), person("Alice", "Davies", "a@x.com", dob="5/8/2008")])[0]
    assert r["date_of_birth"] == datetime(2008, 5, 8)
    [bad] = nok._parse_nok_rows([heading("CDT"), person("Al", "D", "a@x.com", dob="")])[0]
    assert bad["date_of_birth"] is None


def test_a_cdt_group_carries_over_a_page_break():
    # Page 2 of the report repeats the column headings but not the CDT heading.
    _, current = nok._parse_nok_rows([HEADINGS, heading("CDT"), person("A", "B", "a@x.com")])
    records, _ = nok._parse_nok_rows([HEADINGS, person("Macie", "Gibson", "m@x.com")], current)
    assert [r["first_name"] for r in records] == ["Macie"]


def test_rows_before_any_heading_and_layout_rows_are_ignored():
    rows = [
        ["", "", "Next Of Kin - Mail Merge"],    # the layout row wrapping the title
        person("Orphan", "Row", "o@x.com"),      # no heading yet, so not known to be a cadet
        heading("CDT"),
        person("", "", "nobody@x.com"),          # a blank name is nothing to match
        person("Amy", "Able", ""),               # a cadet whose NoK has no email
    ]
    records, _ = nok._parse_nok_rows(rows)
    assert [(r["first_name"], r["email"]) for r in records] == [("Amy", "")]


def test_every_page_of_the_report_is_read(monkeypatch):
    pages = [
        [HEADINGS, heading("CDT"), person("Amy", "Able", "a@x.com")],
        [HEADINGS, person("Bob", "Baker", "b@x.com"), heading("CI"), person("Ci", "One", "c@x.com")],
    ]

    def fake_pager(page, url, viewer_id, on_page):
        assert (url, viewer_id) == (nok.NOK_URL, "rptvwUnitPersonnelNoKMailMerge")
        for cells in pages:
            on_page(cells)
        return len(pages)

    monkeypatch.setattr(nok, "page_through_report", fake_pager)
    monkeypatch.setattr(nok, "read_report_cells", lambda cells: cells)
    assert [r["first_name"] for r in nok.get_parent_emails("page")] == ["Amy", "Bob"]


def test_validating_an_address():
    assert nok.valid_email("jopage123@hoymail.com")  # a typo'd domain is still an address
    assert not nok.valid_email("not an email")
    assert not nok.valid_email("")


# ── matching a row to a cadet ─────────────────────────────────────────────────

def cadet(cin, first, last, dob=None):
    return SimpleNamespace(cin=cin, first_name=first, last_name=last, date_of_birth=dob)


def row(first, last, dob=None):
    return {"first_name": first, "last_name": last, "date_of_birth": dob}


def test_twins_with_one_birthday_are_told_apart_by_forename():
    twins = [cadet(1, "Aiman", "Shahbaz", datetime(2011, 9, 29)), cadet(2, "Iman", "Shahbaz", datetime(2011, 9, 29))]
    assert nok.match_cadet(row("Iman", "Shahbaz", datetime(2011, 9, 29)), twins) == 2
    assert nok.match_cadet(row(" aiman ", "SHAHBAZ"), twins) == 1


def test_a_different_forename_is_matched_on_surname_and_birthday():
    cadets = [cadet(1, "Ben", "Malik", datetime(2014, 1, 22)), cadet(2, "Fatimah", "Malik", datetime(2011, 6, 26))]
    assert nok.match_cadet(row("Benyamin", "Malik", datetime(2014, 1, 22)), cadets) == 1


def test_two_cadets_with_one_name_are_split_by_birthday_or_not_matched():
    cadets = [cadet(1, "Sam", "Smith", datetime(2010, 1, 1)), cadet(2, "Sam", "Smith", datetime(2012, 1, 1))]
    assert nok.match_cadet(row("Sam", "Smith", datetime(2012, 1, 1)), cadets) == 2
    assert nok.match_cadet(row("Sam", "Smith"), cadets) is None  # never a guess


def test_nobody_by_that_name_is_no_match():
    assert nok.match_cadet(row("Ghost", "Nobody", datetime(2010, 1, 1)), [cadet(1, "Amy", "Able")]) is None


# ── merging into what's stored ────────────────────────────────────────────────

def test_bader_addresses_are_added_after_the_stored_ones():
    assert nok.merge_emails("typed@x.com", ["mum@x.com", "Typed@X.com"]) == "typed@x.com;mum@x.com"


def test_nothing_new_leaves_the_stored_value_exactly_as_it_was():
    assert nok.merge_emails("b@x.com;a@x.com", ["A@x.com"]) == "b@x.com;a@x.com"


def test_no_addresses_either_side_is_none():
    assert nok.merge_emails(None, []) is None
    assert nok.merge_emails("", []) is None
    assert nok.merge_emails(None, ["mum@x.com"]) == "mum@x.com"


# ── paging the report ─────────────────────────────────────────────────────────

class FakeButton:
    def __init__(self, page):
        self.page = page

    def is_visible(self):
        return self.page.at < self.page.last


class FakeReport:
    """A report of ``pages`` pages; Next is hidden on the last, as on Bader."""

    def __init__(self, pages, stall_at=None):
        self.at, self.last, self.stall_at, self.urls = 0, pages - 1, stall_at, []
        self.stalled = False

    def goto(self, url):
        self.urls.append(url)

    def wait_for_function(self, script, arg=None, timeout=None):
        if arg is not None and self.stalled:
            raise PlaywrightTimeoutError("no redraw")

    def query_selector(self, selector):
        return FakeButton(self) if "Next" in selector else None


def _fake_waits(monkeypatch):
    monkeypatch.setattr(reports, "wait_for_preloader", lambda page: None)
    monkeypatch.setattr(reports, "wait_for_aspx_load", lambda page: None)

    def click(page, el):
        page.stalled = page.at == page.stall_at
        if not page.stalled:
            page.at += 1
    monkeypatch.setattr(reports, "safe_click", click)


def test_the_pager_reads_each_page_once_and_stops_on_the_last(monkeypatch):
    _fake_waits(monkeypatch)
    page, seen = FakeReport(3), []
    assert reports.page_through_report(page, nok.NOK_URL, nok.NOK_VIEWER, lambda p: seen.append(p.at)) == 3
    assert seen == [0, 1, 2] and page.urls == [nok.NOK_URL]


def test_a_page_that_never_redraws_keeps_what_was_read(monkeypatch):
    _fake_waits(monkeypatch)
    page, seen = FakeReport(3, stall_at=1), []
    assert reports.page_through_report(page, nok.NOK_URL, nok.NOK_VIEWER, lambda p: seen.append(p.at)) == 2
    assert seen == [0, 1]


# ── the cell read, in a real (offline) Chromium ───────────────────────────────

# The report's own nesting, trimmed: layout rows (valign=top) wrapping the data
# table, whose rows are valign=top too. Names and addresses are made up.
_C = '<td class="c"><div>{}</div></td>'
_BLANK = '<td class="c r14">&nbsp;</td>'


def _data_row(*cells):
    return '<tr valign="top"><td style="HEIGHT:6.35mm"></td>' + "".join(cells) + "</tr>"


NOK_REPORT = (
    '<div id="VisibleReportContentctl09"><table><tbody>'
    '<tr valign="top"><td style="HEIGHT:8.38mm"></td><td></td>'
    '<td><table><tbody><tr><td><div>Next Of Kin - Mail Merge</div></td></tr></tbody></table></td><td></td></tr>'
    '<tr valign="top"><td style="HEIGHT:279.36mm"></td><td></td><td colspan="2"><table><tbody>'
    '<tr height="0"><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>'
    + _data_row(*(_C.format(h) for h in HEADINGS[1:]))
    + _data_row(_C.format("CDT"), *[_BLANK] * 6)
    + _data_row(_BLANK, *(_C.format(v) for v in
                          ["3103170099001", "Amy", "Able", "5/8/2008", "Mother", "amy.parent@example.com"]))
    + _data_row(_C.format("CI"), *[_BLANK] * 6)
    + _data_row(_BLANK, *(_C.format(v) for v in
                          ["3103170099002", "Ian", "Instructor", "1/1/1980", "Wife", "ci.kin@example.com"]))
    + "</tbody></table></td></tr></tbody></table></div>"
)


def test_the_report_cells_are_read_once_each_in_a_real_page():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:
            pytest.skip("no Chromium installed")
        page = browser.new_page()
        page.set_content(NOK_REPORT)
        cells = reports.read_report_cells(page)
        browser.close()

    records, _ = nok._parse_nok_rows(cells)
    # Once, not twice: the layout row wrapping the table must not be read as a row.
    assert [(r["first_name"], r["last_name"], r["date_of_birth"], r["email"]) for r in records] == [
        ("Amy", "Able", datetime(2008, 5, 8), "amy.parent@example.com"),
    ]
