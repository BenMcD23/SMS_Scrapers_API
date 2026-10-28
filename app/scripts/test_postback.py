"""fire_postback and open_tab against an offline Chromium page; skipped where
no browser is installed (CI)."""

import pytest

from scripts.tables import fire_postback
from scripts.waiter import open_tab

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


# Bader's __doPostBack is a wrapper that sends nothing unless a link was
# clicked, so the postback has to come from a link click, not a direct call.
RECORDING_POSTBACK = """<form><script>
  window.posted = []; window.clicked = [];
  document.addEventListener('click', e => clicked.push(e.target.tagName));
  window.__doPostBack = (t, a) => posted.push([t, a]);
</script></form>"""


def test_a_postback_is_sent_by_clicking_a_link_with_the_target_and_argument(page):
    page.set_content(RECORDING_POSTBACK)
    # $ and a quote in the argument must survive the round trip into the href.
    assert fire_postback(page, "ctl00$ctl00$cphBaseBody$cphBody$lvCadets$ctrl50$lbFamilyName", "Sort$O'Brien")
    page.wait_for_function("() => posted.length")
    assert page.evaluate("() => posted") == [
        ["ctl00$ctl00$cphBaseBody$cphBody$lvCadets$ctrl50$lbFamilyName", "Sort$O'Brien"]]
    assert page.evaluate("() => clicked") == ["A"]


def test_a_page_without_postbacks_says_so(page):
    page.set_content("<p>no WebForms here</p>")
    assert fire_postback(page, "ctl00$x", "") is False


def test_open_tab_clicks_the_toggle_then_the_item_in_its_menu(page):
    page.set_content("""
      <a id="toggle" onclick="document.getElementById('menu').style.display='block'">Service Record</a>
      <div id="menu" style="display:none"><a id="item" onclick="window.opened = true">Attendance</a></div>""")
    open_tab(page, "#toggle", "#item", timeout=2000)
    assert page.evaluate("() => window.opened") is True


def test_showing_all_rows_waits_for_the_draw_not_a_preloader(page, monkeypatch):
    # The length change is client-side, so the old preloader wait after it only
    # ever sat out its one-second window.
    import scripts.tables as tables
    from scripts.tables import ensure_all_rows_shown

    def no_preloader(*a, **k):
        raise AssertionError("waited for a preloader after a client-side redraw")

    monkeypatch.setattr(tables, "wait_for_preloader", no_preloader)
    page.set_content("""
      <select name="t_length" onchange="setTimeout(draw, 300)">
        <option value="10" selected>10</option><option value="-1">All</option></select>
      <table id="t"><tbody><tr><td>1</td></tr></tbody></table>
      <div id="t_info">Showing 1 to 1 of 12 entries</div>
      <script>function draw() {
        document.querySelector('#t tbody').innerHTML = '<tr><td>x</td></tr>'.repeat(12);
      }</script>""")
    assert ensure_all_rows_shown(page, "t_length", "t", timeout=3000) is True
    assert page.evaluate("() => document.querySelectorAll('#t tbody tr').length") == 12
