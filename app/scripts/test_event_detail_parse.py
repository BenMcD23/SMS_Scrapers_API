"""Parsing the 317 event detail pages straight out of the server-rendered HTML.

These cover the HTTP fast path, which never touches a browser — the browser
fallback in get_317_event_info is what handles anything these can't read.
"""

from scripts.event_scraper import _parse_detail_html, _soup_field

from bs4 import BeautifulSoup


def _field(label, control):
    return f'<div class="form-group"><label>{label}</label>{control}</div>'


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
