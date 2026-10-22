"""Reading the paged SSRS reports under sms.bader.mod.uk/reports/.

Every report there is the same ReportViewer control: one page of rows drawn
into ``VisibleReportContent``, and a Next button that redraws it in place.
Shared by the classification and next-of-kin scrapers so the paging and the
wait-for-redraw logic live in one place.
"""

import logging

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from scripts.waiter import safe_click, wait_for_aspx_load, wait_for_preloader

logger = logging.getLogger(__name__)

CONTENT = "div[id^='VisibleReportContent']"
MAX_PAGES = 100


def _signature(page: Page):
    try:
        el = page.query_selector(CONTENT)
        return el.inner_text()[:300] if el else ""
    except Exception:
        return ""


def read_report_cells(page: Page) -> list[list[str]]:
    """Cell texts of every ``tr[valign=top]`` on the drawn page, in one round trip.

    Only a row's own cells (``:scope > td``): the report nests its table inside
    layout rows, and counting their descendants too would read every data cell a
    second time as part of the row wrapping it.
    """
    return page.evaluate(
        """(sel) => {
            const el = document.querySelector(sel);
            if (!el) return [];
            return Array.from(el.querySelectorAll("tr[valign='top']")).map(tr =>
                Array.from(tr.querySelectorAll(':scope > td')).map(td => td.innerText.trim()));
        }""",
        CONTENT,
    )


def page_through_report(page: Page, url: str, viewer_id: str, on_page) -> int:
    """Open a report and call ``on_page(page)`` once per page. Returns pages read.

    ``viewer_id`` is the report control's own name (``rptvwReport``,
    ``rptvwUnitPersonnelNoKMailMerge``), which is all that differs in the Next
    button's id. Stops on the last page, or if a click doesn't redraw within 30s
    (logged; what was read so far is kept). Raises if the first page never draws.
    """
    page.goto(url)
    wait_for_preloader(page)
    wait_for_aspx_load(page)

    page.wait_for_function(
        f"() => document.querySelectorAll(\"{CONTENT} tr[valign='top']\").length > 3",
        timeout=60000,
    )

    next_id = f"ctl00_ctl00_cphBaseBody_cphBody_{viewer_id}_ctl05_ctl00_Next_ctl00_ctl00"
    pages = 0
    while True:
        on_page(page)
        pages += 1

        # On the last page the enabled Next button is still there, just hidden.
        next_btn = page.query_selector(f"#{next_id}")
        if not next_btn or not next_btn.is_visible() or pages >= MAX_PAGES:
            break

        before = _signature(page)
        safe_click(page, next_btn)
        try:
            page.wait_for_function(
                f"(before) => {{ const el = document.querySelector(\"{CONTENT}\"); "
                "return !!el && el.innerText.slice(0,300) !== before && el.innerText.length > 0; }",
                arg=before,
                timeout=30000,
            )
        except PlaywrightTimeoutError:
            logger.warning(f"Warning: {viewer_id} report stopped advancing at page {pages}")
            break
        wait_for_aspx_load(page)

    return pages
