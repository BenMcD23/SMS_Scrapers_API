from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError


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
