"""Contact details off a cadet's Bader profile, for the contact details audit.

Two places: the cadet's own Contact Details grid on the profile summary (the
page a profile opens on), and the Contacts tab, which lists parents and other
contacts by name only. Each contact's phone numbers and emails are behind its
View button, so every contact costs a postback, and the tab is reopened
between them to get the list back.

Both readers return None, never [], when the page didn't load the way we
expect, so the save keeps what's stored rather than recording "no contacts".
"""
import logging

from playwright.sync_api import Page

from scripts.waiter import safe_click, wait_for_aspx_load, wait_for_preloader

logger = logging.getLogger(__name__)

OWN_GRID = "ctl00_ctl00_cphBaseBody_cphBody_ContactMethodsGridView"
# The profile's tab strip is TabsCadet1 (TabsCadet once another tab is open);
# keyed on that so a "Contacts" link anywhere else on Bader can't match.
CONTACTS_TAB = "xpath=//a[contains(@id, 'TabsCadet') and contains(normalize-space(.), 'Contacts')]"
CONTACTS_GRID = "table[id$='ContactsGridView']"

# Every grid with "Method" and "Detail" headings: the cadet's own and, on a
# contact's View, theirs. Found by its headings rather than an id because the
# View's id isn't known; `visible` tells the contact's apart from a grid left
# in a hidden tab. Body rows only, so the footer's "add" row is never read.
_READ_METHOD_GRIDS = """() => Array.from(document.querySelectorAll('table')).filter(t => {
    const heads = Array.from(t.querySelectorAll('thead th')).map(th => th.innerText.trim());
    return heads.includes('Method') && heads.includes('Detail');
}).map(t => ({
    id: t.id || '',
    visible: !!(t.offsetWidth || t.offsetHeight || t.getClientRects().length),
    rows: Array.from(t.querySelectorAll('tbody > tr')).map(tr =>
        Array.from(tr.querySelectorAll('td')).map(td => td.innerText)),
}))"""

_HAS_VISIBLE_METHOD_GRID = "() => (" + _READ_METHOD_GRIDS + ")().some(g => g.visible)"

_READ_CONTACT_ROWS = """(sel) => {
    const table = document.querySelector(sel);
    if (!table) return null;
    return Array.from(table.querySelectorAll('tbody > tr')).map(tr => {
        const box = tr.querySelector("input[type='checkbox']");
        const view = tr.querySelector("a[id$='lbViewContact']");
        return {
            cells: Array.from(tr.querySelectorAll('td')).map(td => td.innerText),
            nextOfKin: !!(box && box.checked),
            viewId: view ? view.id : null,
        };
    });
}"""


def _parse_methods(rows) -> list[dict]:
    """[{"method", "detail"}] out of a grid's body rows. DataTables' "No data
    available" row has one cell, so a grid with nothing in it gives []."""
    out = []
    for cells in rows or []:
        if len(cells) < 2:
            continue
        method, detail = cells[0].strip(), cells[1].strip()
        if method and detail:
            out.append({"method": method, "detail": detail})
    return out


def _pick_contact_grid(grids):
    """The rows of the grid a contact's View put on screen, or None.

    Prefers a visible grid that isn't the cadet's own, in case the profile
    summary's grid is still in the page behind the tab.
    """
    visible = [g for g in grids or [] if g.get("visible")]
    theirs = [g for g in visible if g.get("id") != OWN_GRID] or visible
    return theirs[0]["rows"] if theirs else None


def _parse_contact_rows(rows) -> list[dict]:
    """Contacts out of the Contacts grid: Name, Title, Relationship, Next of
    Kin, Options. Rows without a View button (the empty-table row) are skipped."""
    out = []
    for row in rows or []:
        cells = [c.strip() for c in row.get("cells") or []]
        if len(cells) < 3 or not cells[0] or not row.get("viewId"):
            continue
        out.append({
            "name": cells[0],
            "title": cells[1] or None,
            "relationship": cells[2] or None,
            "next_of_kin": bool(row.get("nextOfKin")),
            "view_id": row["viewId"],
        })
    return out


def get_own_contact_methods(page: Page):
    """The cadet's contact methods from the profile summary, or None if the
    grid isn't there. Call before opening any tab: it's only on the summary."""
    try:
        page.wait_for_selector(f"#{OWN_GRID}", state="attached", timeout=5000)
        grids = page.evaluate(_READ_METHOD_GRIDS)
        own = next((g for g in grids if g.get("id") == OWN_GRID), None)
        return _parse_methods(own["rows"]) if own else None
    except Exception as e:
        logger.error(f"Warning: Could not read the cadet's contact details: {e}")
        return None


def _open_contacts_tab(page: Page):
    tab = page.wait_for_selector(CONTACTS_TAB, state="attached", timeout=15000)
    # Clicked in the page, like the Medical tab: the link may sit in a
    # dropdown that isn't open, which Playwright's click would wait on.
    tab.evaluate("el => el.click()")
    wait_for_preloader(page)
    wait_for_aspx_load(page)
    page.wait_for_selector(CONTACTS_GRID, state="attached", timeout=15000)


def _read_contact_methods(page: Page, view_id: str):
    safe_click(page, page.wait_for_selector(f"#{view_id}", state="attached", timeout=10000))
    wait_for_preloader(page)
    wait_for_aspx_load(page)
    page.wait_for_function(_HAS_VISIBLE_METHOD_GRID, timeout=10000)
    rows = _pick_contact_grid(page.evaluate(_READ_METHOD_GRIDS))
    if rows is None:
        raise RuntimeError("the contact's details didn't open")
    return _parse_methods(rows)


def get_contacts(page: Page, warn=None):
    """Every contact on the Contacts tab with their contact methods, in Bader's
    order, or None if the tab or any contact's details couldn't be read: a
    half-read list would drop a parent from the audit, so it's all or nothing.

    ``warn`` gets the reason, so the scrape's own log says which cadet and
    why; the audit page can only say "not checked".
    """
    step = "the Contacts tab didn't open"
    try:
        _open_contacts_tab(page)
        step = "the contacts list couldn't be read"
        contacts = _parse_contact_rows(page.evaluate(_READ_CONTACT_ROWS, CONTACTS_GRID))
        for i, contact in enumerate(contacts):
            step = f"{contact['name']}'s details didn't open"
            if i:
                _open_contacts_tab(page)  # the View replaced the list
            contact["contact_methods"] = _read_contact_methods(page, contact.pop("view_id"))
        return contacts
    except Exception as e:
        logger.error(f"Warning: Could not read the cadet's contacts: {step}: {e}")
        if warn:
            warn(f"{step} ({str(e).splitlines()[0] if str(e) else type(e).__name__})")
        return None
