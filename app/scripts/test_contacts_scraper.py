"""Reading contact details off a Bader profile.

The parsers are pinned on their own; the readers run against an offline
Chromium page built from Bader's markup (skipped where no browser is
installed). The Contacts grid and the cadet's own grid are Bader's HTML,
trimmed; what a contact's View shows is a stand-in, as it's the one page we
haven't seen, so it's built to be the least we rely on: a grid headed Method
and Detail.
"""

import json

import pytest

from scripts.contacts_scraper import (
    OWN_GRID,
    _parse_contact_rows,
    _parse_methods,
    _pick_contact_grid,
    get_contacts,
    get_own_contact_methods,
)

# ── Parsers ───────────────────────────────────────────────────────────────────

def test_methods_keep_bader_names_and_trim_details():
    rows = [["\n  Mobile Phone\n", " 07904953362 "], ["Primary Email", "a@317atc.co.uk"]]
    assert _parse_methods(rows) == [
        {"method": "Mobile Phone", "detail": "07904953362"},
        {"method": "Primary Email", "detail": "a@317atc.co.uk"},
    ]


def test_an_empty_grid_is_no_methods_not_an_error():
    # DataTables' placeholder is a single cell spanning the table.
    assert _parse_methods([["No data available in table"]]) == []
    assert _parse_methods([]) == []
    assert _parse_methods(None) == []


def test_a_row_without_a_detail_is_skipped():
    assert _parse_methods([["Home Phone", "  "]]) == []


def test_the_contact_grid_is_the_visible_one_that_isnt_the_cadets():
    own = {"id": OWN_GRID, "visible": True, "rows": [["Mobile Phone", "1"]]}
    theirs = {"id": "x_ContactMethodsGridView", "visible": True, "rows": [["Mobile Phone", "2"]]}
    hidden = {"id": "y", "visible": False, "rows": [["Mobile Phone", "3"]]}
    assert _pick_contact_grid([own, hidden, theirs]) == [["Mobile Phone", "2"]]
    # The View may reuse the cadet's grid id on its own page.
    assert _pick_contact_grid([own]) == [["Mobile Phone", "1"]]
    assert _pick_contact_grid([hidden]) is None
    assert _pick_contact_grid([]) is None


def test_contact_rows_read_name_title_relationship_and_next_of_kin():
    rows = [
        {"cells": ["Marian Adeniyi", "Miss", "Mother", "", "View Delete"], "nextOfKin": True, "viewId": "v2"},
        {"cells": ["Olufunke Adeniyi", "", "Grandparent", "", "View"], "nextOfKin": False, "viewId": "v3"},
    ]
    assert _parse_contact_rows(rows) == [
        {"name": "Marian Adeniyi", "title": "Miss", "relationship": "Mother", "next_of_kin": True, "view_id": "v2"},
        {"name": "Olufunke Adeniyi", "title": None, "relationship": "Grandparent", "next_of_kin": False,
         "view_id": "v3"},
    ]


def test_the_empty_contacts_row_is_not_a_contact():
    assert _parse_contact_rows([{"cells": ["No data available in table"], "viewId": None}]) == []
    assert _parse_contact_rows([{"cells": ["Someone", "Mr", "Father"], "viewId": None}]) == []


# ── Against a page ────────────────────────────────────────────────────────────

sync_api = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:
            pytest.skip("no Chromium installed")
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    page = browser.new_page()
    yield page
    page.close()


def _method_row(n, method, detail):
    p = "ctl00_ctl00_cphBaseBody_cphBody_ContactMethodsGridView"
    return f"""<tr><td class="form-group"><span id="{p}_ctl0{n}_lblContactType">{method}</span></td>
      <td class="form-group"><span id="{p}_ctl0{n}_lblDetail">{detail}</span></td>
      <td class="text-right"><a class="btn">Edit</a></td></tr>"""


OWN_GRID_HTML = f"""
<h4 class="card-title">Contact Details</h4>
<table id="{OWN_GRID}"><thead><tr><th>Method</th><th>Detail</th><th>Options</th></tr></thead>
<tbody>
  {_method_row(2, "Mobile Phone", "07904953362")}
  {_method_row(4, "Personal Account (Bader)", "oluwapelumi.aderele100@rafac.mod.gov.uk")}
  {_method_row(3, "Primary Email", "aderele@317atc.co.uk")}
</tbody>
<tfoot><tr><td><select><option>Select ...</option></select></td><td><input type="text"></td>
  <td><a>Add</a></td></tr></tfoot>
</table>"""


def _contact_row(n, name, title, relation, nok):
    p = "ctl00_ctl00_cphBaseBody_cphBody_ContactsGridView"
    checked = 'checked=""' if nok else ""
    return f"""<tr><td>{name}</td><td>{title}</td><td>{relation}</td>
      <td><input type="checkbox" id="cbNoK" {checked} disabled=""><label for="cbNoK"></label></td>
      <td class="text-right">
        <a id="{p}_ctl0{n}_lbViewContact" title="View"
           href="javascript:__doPostBack('ctl00$ctl00$cphBaseBody$cphBody$ContactsGridView$ctl0{n}$lbViewContact','')">
           <span>View</span></a>
        <a id="{p}_ctl0{n}_lbDeleteContact" title="Delete" href="#">Delete</a>
      </td></tr>"""


CONTACTS = {
    "ctl02": ("Marian Adeniyi", "Miss", "Mother", True,
              [("Mobile Phone", "07700 900111"), ("Primary Email", "marian@example.com")]),
    "ctl03": ("Olufunke Adeniyi", "Mrs", "Grandparent", False, [("Home Phone", "0161 496 0000")]),
}


def _profile(contacts=CONTACTS, broken_view=None):
    """A profile summary with a Contacts tab. The tab and each View swap the
    content the way Bader's postbacks do; ``broken_view`` names a contact
    whose View shows nothing."""
    grid = "".join(_contact_row(k[-1], *v[:4]) for k, v in contacts.items()) or (
        '<tr class="odd"><td valign="top" colspan="5" class="dataTables_empty">No data available in table</td></tr>')
    views = {
        k: "".join(f"<tr><td><span>{m}</span></td><td><span>{d}</span></td><td></td></tr>" for m, d in v[4])
        for k, v in contacts.items()
    }
    return f"""<form>
<a id="ctl00_ctl00_cphBaseBody_cphBody_TabsCadet1_Contacts" href="#"
   onclick="document.getElementById('content').innerHTML = window.contactsGrid; return false;">Contacts</a>
<a href="#">Contacts help</a>
<div id="content">{OWN_GRID_HTML}</div>
<script>
  window.contactsGrid = `<table id="ctl00_ctl00_cphBaseBody_cphBody_ContactsGridView">
    <thead><tr><th>Name</th><th>Title</th><th>Relationship</th><th>Next of Kin</th><th>Options</th></tr></thead>
    <tbody>{grid}</tbody></table>`;
  const views = {json.dumps(views)};
  const broken = {json.dumps(broken_view)};
  window.__doPostBack = (target) => {{
    const key = target.split('$').slice(-2)[0];
    document.getElementById('content').innerHTML = key === broken ? '<p>Error</p>' :
      `<table id="ctl00_ctl00_cphBaseBody_cphBody_ContactMethodsGridView">
        <thead><tr><th>Method</th><th>Detail</th><th>Options</th></tr></thead>
        <tbody>${{views[key]}}</tbody></table>`;
  }};
</script></form>"""


def test_the_cadets_own_methods_come_off_the_summary_without_the_add_row(page):
    page.set_content(_profile())
    assert get_own_contact_methods(page) == [
        {"method": "Mobile Phone", "detail": "07904953362"},
        {"method": "Personal Account (Bader)", "detail": "oluwapelumi.aderele100@rafac.mod.gov.uk"},
        {"method": "Primary Email", "detail": "aderele@317atc.co.uk"},
    ]


def test_no_grid_on_the_page_is_none_so_the_stored_methods_stay(page):
    page.set_content("<p>Not a profile</p>")
    assert get_own_contact_methods(page) is None


def test_every_contact_is_read_with_their_own_methods(page):
    page.set_content(_profile())
    assert get_contacts(page) == [
        {"name": "Marian Adeniyi", "title": "Miss", "relationship": "Mother", "next_of_kin": True,
         "contact_methods": [{"method": "Mobile Phone", "detail": "07700 900111"},
                             {"method": "Primary Email", "detail": "marian@example.com"}]},
        {"name": "Olufunke Adeniyi", "title": "Mrs", "relationship": "Grandparent", "next_of_kin": False,
         "contact_methods": [{"method": "Home Phone", "detail": "0161 496 0000"}]},
    ]


def test_a_cadet_with_no_contacts_reads_as_an_empty_list(page):
    page.set_content(_profile(contacts={}))
    assert get_contacts(page) == []


def test_one_contact_that_wont_open_drops_the_whole_read(page):
    # Saving only Marian would tell the audit the grandparent was never there.
    page.set_content(_profile(broken_view="ctl03"))
    warnings = []
    assert get_contacts(page, warn=warnings.append) is None
    # Says whose details failed, so the run log points at the contact on Bader.
    assert len(warnings) == 1 and warnings[0].startswith("Olufunke Adeniyi's details didn't open")


def test_no_contacts_tab_is_none(page):
    page.set_content("<p>Not a profile</p>")
    warnings = []
    assert get_contacts(page, warn=warnings.append) is None
    assert warnings[0].startswith("the Contacts tab didn't open")
