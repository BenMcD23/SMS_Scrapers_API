from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


def wait_for_aspx_load(page: Page, timeout: int = 30000):
    page.wait_for_load_state("domcontentloaded", timeout=timeout)
    try:
        page.evaluate("""
            () => new Promise((resolve) => {
                const deadline = Date.now() + 25000;
                const check = () => {
                    try {
                        if (document.readyState !== 'complete') {
                            if (Date.now() < deadline) setTimeout(check, 100); else resolve();
                            return;
                        }
                        if (typeof Sys !== 'undefined' &&
                            Sys.WebForms.PageRequestManager.getInstance().get_isInAsyncPostBack()) {
                            if (Date.now() < deadline) setTimeout(check, 100); else resolve();
                        } else { resolve(); }
                    } catch (e) { resolve(); }
                };
                check();
            })
        """)
    except Exception:
        pass


def wait_for_preloader(page: Page, timeout: int = 20000):
    try:
        page.locator(".preloader").wait_for(state="visible", timeout=1000)
        page.locator(".preloader").wait_for(state="hidden", timeout=timeout)
    except PlaywrightTimeoutError:
        pass
    except Exception:
        pass


def safe_click(page: Page, element):
    try:
        element.click()
    except Exception:
        try:
            element.evaluate("el => el.click()")
        except Exception:
            pass


def open_tab(page: Page, dropdown: str, item: str, timeout: int = 15000):
    """Open a profile tab: the dropdown toggle, then the item in it.

    Only the item is a postback. The toggle just shows the menu (Bootstrap
    stops its href), so waiting for a preloader after it only ever sat out
    wait_for_preloader's one-second window. The item click itself waits for
    the menu to show it.
    """
    safe_click(page, page.wait_for_selector(dropdown, timeout=timeout))
    safe_click(page, page.wait_for_selector(item, state="attached", timeout=timeout))
    wait_for_preloader(page)
    wait_for_aspx_load(page)
