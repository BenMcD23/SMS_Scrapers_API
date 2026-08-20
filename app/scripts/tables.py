"""Shared helpers for the DataTables-backed lists and the WebForms links in them.

These generalise what the event scraper worked out: a ``select_option`` on a
DataTables length control redraws every row and is the slowest single thing in
a scrape loop, so it should happen once rather than once per person; and a
profile that is opened by a ``__doPostBack`` link does not need that redraw at
all, because the postback target is a server-side control name and the server
resolves it whether or not DataTables has that row in the DOM.
"""
import re

from playwright.sync_api import Page

from scripts.waiter import wait_for_aspx_load, wait_for_preloader


# ASP.NET renders a LinkButton as
# href="javascript:__doPostBack('ctl00$...$lvCadets$ctrl0$lbFamilyName','')".
_POSTBACK_RE = re.compile(
    r"__doPostBack\(\s*(?P<q>['\"])(?P<target>.*?)(?P=q)\s*,"
    r"\s*(?P<q2>['\"])(?P<argument>.*?)(?P=q2)\s*\)"
)

# "Showing 1 to 25 of 2,047 entries" — on a filtered table the first count is
# the filtered one, which is also the one that gets drawn.
_ENTRIES_RE = re.compile(r"of\s+([\d,]+)\s+entries")


def postback_target(href: str):
    """(target, argument) for a ``__doPostBack`` href, or None if it isn't one.

    Anything else — a real href, ``#``, an empty attribute — returns None so the
    caller falls back to clicking the element.
    """
    match = _POSTBACK_RE.search(href or "")
    if not match:
        return None
    return match.group("target"), match.group("argument")


def entries_total(info_text: str):
    """Row count DataTables claims in its info line, or None."""
    match = _ENTRIES_RE.search(info_text or "")
    return int(match.group(1).replace(",", "")) if match else None


def fire_postback(page: Page, target: str, argument: str = "") -> bool:
    """Submit the WebForms postback a LinkButton would, without needing its row.

    Returns False when the page has no ``__doPostBack`` (so the caller takes the
    click path). The call is deferred by a ``setTimeout`` because the navigation
    it triggers otherwise tears down the execution context before ``evaluate``
    can return, which surfaces as an exception on a call that actually worked.
    """
    try:
        if not page.evaluate("() => typeof __doPostBack === 'function'"):
            return False
        page.evaluate(
            "([t, a]) => { setTimeout(() => __doPostBack(t, a), 0); }",
            [target, argument],
        )
    except Exception:
        return False
    return True


def wait_for_full_draw(page: Page, table_id: str, timeout: int = 20000) -> bool:
    """Wait until the table has as many rows in the DOM as its info line claims.

    This is what the fixed ``sleep`` after a length change was standing in for.
    Waiting on the actual condition is both quicker in the normal case and
    correct in the slow one: a caller that full-replaces stored history off a
    half-drawn table would throw away the rows that hadn't rendered yet.
    """
    try:
        page.wait_for_function(
            """(id) => {
                const body = document.querySelector('#' + id + ' tbody');
                if (!body) return false;
                const rows = body.querySelectorAll('tr').length;
                const info = document.querySelector('#' + id + '_info');
                if (!info) return rows > 0;
                const m = /of\\s+([\\d,]+)\\s+entries/.exec(info.innerText || '');
                if (!m) return rows > 0;
                return rows >= parseInt(m[1].replace(/,/g, ''), 10);
            }""",
            arg=table_id,
            timeout=timeout,
        )
        return True
    except Exception:
        return False


def ensure_all_rows_shown(
    page: Page,
    select_name: str,
    table_id: str = None,
    expected_rows: int = None,
    required: bool = True,
    timeout: int = 20000,
) -> bool:
    """Put a DataTable into "show all rows", but only when it isn't already.

    ``select_option`` fires a change event whatever the current value, and
    DataTables re-renders every row on it — with the length set to -1 that is
    the slowest single operation in a scrape loop, so skip it when nothing
    would change.

    ``required=False`` tolerates the control being absent, which is what a table
    with too few rows to paginate looks like.
    """
    select = page.locator(f"[name='{select_name}']")
    try:
        select.wait_for(timeout=timeout)
    except Exception:
        if required:
            raise
        return False

    already_all = select.input_value() == "-1"
    if already_all and table_id is not None:
        # A postback can leave the dropdown reading -1 while DataTables has gone
        # back to rendering a single page, so trust the row count over the value.
        already_all = _drawn_rows(page, table_id) >= (expected_rows or 1)
    if already_all:
        return True

    select.select_option(value="-1")
    wait_for_preloader(page)
    wait_for_aspx_load(page)
    if table_id is not None:
        wait_for_full_draw(page, table_id, timeout=timeout)
    return True


def _drawn_rows(page: Page, table_id: str) -> int:
    try:
        return page.evaluate(
            "(id) => document.querySelectorAll('#' + id + ' tbody tr').length",
            table_id,
        )
    except Exception:
        return 0


def read_rows(page: Page, selector: str):
    """Every row of a table as a list of cell texts, in one round trip.

    Walking a table with ``query_selector_all`` + ``inner_text()`` costs a CDP
    round trip per cell, which is minutes for a register going back to 2009.
    """
    try:
        return page.evaluate(
            "(sel) => Array.from(document.querySelectorAll(sel + ' tbody tr'))"
            ".map(tr => Array.from(tr.querySelectorAll('td')).map(td => td.innerText))",
            selector,
        )
    except Exception:
        return []
