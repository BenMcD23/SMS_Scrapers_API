"""Parsing the 317 event detail pages straight out of the server-rendered HTML.

These cover the HTTP fast path, which never touches a browser — the browser
fallback in get_317_event_info is what handles anything these can't read.
"""

from bs4 import BeautifulSoup

from scripts.event_scraper import _parse_category, _parse_detail_html, _soup_field


def _field(label, control):
    return f'<div class="form-group"><label>{label}</label>{control}</div>'


# Bader's category picker as the server sends it: a plain select with the
# chosen option marked. select2 draws the visible box in the browser.
CATEGORY_SELECT = (
    '<select name="ctl00$ctl00$cphBaseBody$cphBody$category" id="category" class="select2">'
    '<option value="">Please select</option>'
    '<option value="7">RAFAC Gliding</option>'
    '<option selected="selected" value="12">\n      Road Marching\n    </option>'
    "</select>"
)

DETAIL_PAGE = "<html><body><form>" + "".join([
    _field("Title", '<input type="text" value="317 EFA 2026" />'),
    _field("Reference", '<input type="text" value="SQN/0317/FAD/26/0001" />'),
    _field("Adult IC", '<input type="text" value="Fg Off A Smith" />'),
    _field("Date From", '<input type="text" value="12/08/2026 22:12" />'),
    _field("Date To", '<input type="text" value="14/08/2026 22:13" />'),
    _field("Contact No.", '<input type="text" value="07700900123" />'),
    _field("Location", '<input type="text" value="Failsworth ATC HQ" />'),
    _field("Postcode", '<input type="text" value="M35 0AA" />'),
    _field("Cost Per Cadet", '<input type="text" value="12.50" />'),
    _field("Dress", '<input type="text" value="No.2 Uniform" />'),
    _field("Description", "<textarea>&lt;p&gt;Two day EFA course.&lt;/p&gt;</textarea>"),
    _field("Category", CATEGORY_SELECT),
]) + "</form></body></html>"


def test_parses_every_field():
    fields = _parse_detail_html(DETAIL_PAGE)
    assert fields == {
        "title": "317 EFA 2026",
        "reference": "SQN/0317/FAD/26/0001",
        "adult_ic": "Fg Off A Smith",
        "date_from": "12/08/2026 22:12",
        "date_to": "14/08/2026 22:13",
        "contact_number": "07700900123",
        "location_name": "Failsworth ATC HQ",
        "postcode": "M35 0AA",
        "cost": "12.50",
        "dress": "No.2 Uniform",
        # Entities are unescaped like el.value, then clean_html strips the markup
        "description": "Two day EFA course.",
        "category": "Road Marching",
    }


def test_login_bounce_is_rejected():
    """A timed-out session redirects to the login form; writing those blanks
    over the events table would wipe it."""
    login = '<html><body><input name="txtUsername" /><input name="txtPassword" /></body></html>'
    assert _parse_detail_html(login) is None


def test_missing_title_is_rejected():
    assert _parse_detail_html("<html><body><p>Access denied</p></body></html>") is None


def test_label_matching_takes_the_first_match_in_document_order():
    """'Date From' also contains 'Date', and 'Location' appears in other labels —
    the xpath this replaced took the first matching label, so this must too."""
    html = (
        _field("Date From", '<input value="01/01/2026 09:00" />')
        + _field("Date To", '<input value="02/01/2026 17:00" />')
    )
    soup = BeautifulSoup(html, "html.parser")
    assert _soup_field(soup, "Date From", "input") == "01/01/2026 09:00"
    assert _soup_field(soup, "Date To", "input") == "02/01/2026 17:00"


def test_absent_label_and_empty_value_are_blank_not_an_error():
    soup = BeautifulSoup(_field("Title", '<input value="" />'), "html.parser")
    assert _soup_field(soup, "Title", "input") == ""
    assert _soup_field(soup, "Nonexistent", "input") == ""


def test_values_are_stripped():
    soup = BeautifulSoup(
        _field("Dress", '<input value="  No.2 Uniform  " />')
        + _field("Description", "<textarea>\n  Some text.\n</textarea>"),
        "html.parser",
    )
    assert _soup_field(soup, "Dress", "input") == "No.2 Uniform"
    assert _soup_field(soup, "Description", "textarea") == "Some text."


def test_js_populated_date_falls_back_to_the_browser():
    """A datepicker that fills its input after load leaves no value attribute to
    read, so the whole page must go to the browser rather than be stored blank."""
    html = DETAIL_PAGE.replace('<input type="text" value="12/08/2026 22:12" />', "<input type='text' />")
    assert _parse_detail_html(html) is None


# ── category ──────────────────────────────────────────────────────────────────

def _category(html):
    return _parse_category(BeautifulSoup(html, "html.parser"))


def test_category_is_the_selected_option_with_its_padding_collapsed():
    assert _category(CATEGORY_SELECT) == "Road Marching"


def test_category_falls_back_to_the_select2_box():
    """The pasted live markup: if all we have is select2's rendered box, its
    title holds the name, and the WHQ/RHQ badges must not leak into it."""
    html = (
        '<span class="select2-selection__rendered" id="select2-category-container" title="\n'
        '            Road Marching\n        "><span>Road Marching'
        '<span class="ml-1 badge badge-success">WHQ</span>'
        '<span class="ml-1 badge badge-success">RHQ</span></span></span>'
    )
    assert _category(html) == "Road Marching"


def test_flying_category_reads_in_full():
    html = CATEGORY_SELECT.replace(' selected="selected"', "").replace(
        '<option value="7">', '<option value="7" selected>'
    )
    assert _category(html) == "RAFAC Gliding"


def test_no_category_chosen_or_no_picker_is_none_not_the_placeholder():
    assert _category(CATEGORY_SELECT.replace(' selected="selected"', "")) is None
    assert _category("<p>No picker here</p>") is None


def test_a_page_without_a_category_still_parses_the_rest():
    fields = _parse_detail_html(DETAIL_PAGE.replace(CATEGORY_SELECT, ""))
    assert fields["title"] == "317 EFA 2026" and fields["category"] is None


def test_event_details_keeps_only_the_pages_that_parsed(monkeypatch):
    """Pages that failed to fetch, bounced to login, or have no id are left
    out, so the caller sees "unknown" rather than a blank category."""
    import scripts.event_scraper as scraper

    login = '<html><body><input name="txtUsername" /></body></html>'
    pages = {
        scraper.EVENT_DETAIL_URL.format("1"): DETAIL_PAGE,
        scraper.EVENT_DETAIL_URL.format("2"): login,
    }
    asked = []

    def fake_fetch(page, links, messages, lock):
        asked.extend(links)
        return pages

    monkeypatch.setattr(scraper, "_fetch_detail_pages", fake_fetch)
    details, fetched = scraper.get_event_details("page", ["1", "2", "3", None], [], None)
    assert set(details) == {"1"} and details["1"]["category"] == "Road Marching"
    assert fetched is pages
    assert len(asked) == 3  # the None id is never requested
