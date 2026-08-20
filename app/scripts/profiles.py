"""Opening a person's profile from a Bader list page.

Cadets and staff use the same shape — a DataTable of people whose surname cell
is an ASP.NET LinkButton — so both scrapers open profiles through here.

The point of this module is what it *doesn't* do. Opening row N used to mean
reloading the list, redrawing every row so that row N was in the DOM, and then
clicking it; the redraw is the expensive half and it was paid once per person.
A LinkButton's postback names a server-side control, so the server resolves it
whether or not DataTables has drawn that row — which makes the redraw
unnecessary. The click path is kept as a fallback for anything that doesn't
come back looking like a profile.
"""
import re

from playwright.sync_api import Page

from scripts.tables import ensure_all_rows_shown, fire_postback, postback_target
from scripts.waiter import wait_for_aspx_load, wait_for_preloader, safe_click

CADETS_URL = "https://sms.bader.mod.uk/cadets/default.aspx"
STAFF_URL = "https://sms.bader.mod.uk/staff/default.aspx"

CADETS = {
    "url": CADETS_URL,
    "length_select": "Cadets_length",
    "table_id": "Cadets",
    "link_marker": "lbFamilyName",
    "link_id": "ctl00_ctl00_cphBaseBody_cphBody_lvCadets_ctrl{}_lbFamilyName",
}

STAFF = {
    "url": STAFF_URL,
    "length_select": "Staff_length",
    "table_id": "Staff",
    "link_marker": "lnkFamilyName",
    "link_id": "ctl00_ctl00_cphBaseBody_cphBody_lvStaff_ctrl{}_lnkFamilyName",
}

# The one marker a cadet profile and a staff profile share. Both scrapers go on
# to open tabs underneath it, so if it isn't there we aren't on a profile.
PROFILE_MARKER = "xpath=//a[contains(text(), 'Service Record')]"

_INDEX_RE = re.compile(r"_ctrl(\d+)_")


def link_index(element_id: str):
    """Row index out of ``..._lvCadets_ctrl12_lbFamilyName``, or None.

    This is the same index the scrapers already address rows by, so the postback
    map and the click fallback stay keyed the same way.
    """
    match = _INDEX_RE.search(element_id or "")
    return int(match.group(1)) if match else None


def collect_profile_links(page: Page, link_marker: str) -> dict:
    """{row index: (postback target, argument)} for the list page as drawn.

    Called once, while the caller has the table fully drawn anyway to read the
    names off it — so the expensive redraw is paid a single time per scrape
    instead of once per person.
    """
    try:
        anchors = page.evaluate(
            "(marker) => Array.from(document.querySelectorAll('a[id*=\"' + marker + '\"]'))"
            ".map(a => [a.id, a.getAttribute('href')])",
            link_marker,
        )
    except Exception:
        return {}

    links = {}
    for element_id, href in anchors:
        index = link_index(element_id)
        target = postback_target(href)
        if index is not None and target:
            links[index] = target
    return links


def _profile_is_open(page: Page) -> bool:
    try:
        page.wait_for_selector(PROFILE_MARKER, timeout=5000)
        return True
    except Exception:
        return False


class _NoPostback(Exception):
    """The page has no __doPostBack — take the click path."""


def _open_by_postback(page: Page, postback) -> bool:
    """Fire the row's postback and say whether it landed on a profile.

    The navigation is awaited explicitly: fire_postback defers the call so the
    navigation can't tear down its execution context, which means control comes
    back here before the browser has started loading anything. Without the wait,
    wait_for_load_state would happily report the *list* page as loaded.
    """
    try:
        with page.expect_navigation(timeout=20000):
            fired = fire_postback(page, *postback)
            if not fired:
                # Bail out of the wait rather than sit on it for 20s.
                raise _NoPostback()
    except _NoPostback:
        return False
    except Exception:
        # A postback that stays on the page (an UpdatePanel) never navigates;
        # the profile check below is what decides whether it worked.
        pass

    wait_for_preloader(page)
    wait_for_aspx_load(page)
    return _profile_is_open(page)


def open_profile(page: Page, index: int, links: dict, kind: dict, expected_rows: int = None) -> bool:
    """Open list row ``index``'s profile. True if the postback fast path worked.

    Either way the page is left on the profile, or an exception is raised by the
    fallback — callers see the same state they did when this was a redraw and a
    click.
    """
    page.goto(kind["url"])
    wait_for_aspx_load(page)

    postback = links.get(index)
    if postback:
        if _open_by_postback(page, postback):
            return True
        # Postback didn't land on a profile — start the slow path from a clean
        # list rather than from whatever we ended up on.
        page.goto(kind["url"])
        wait_for_aspx_load(page)

    ensure_all_rows_shown(
        page, kind["length_select"], kind["table_id"], expected_rows, required=False
    )
    link = page.wait_for_selector(f"#{kind['link_id'].format(index)}", timeout=20000)
    safe_click(page, link)
    wait_for_preloader(page)
    wait_for_aspx_load(page)
    return False
