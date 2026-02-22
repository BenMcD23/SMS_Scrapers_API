from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException
from selenium.common.exceptions import ElementClickInterceptedException

def wait_for_aspx_load(driver, timeout=30):
    """
    Waits for the document to be ready and for any 
    ASP.NET AJAX partial postbacks to complete.
    """
    wait = WebDriverWait(driver, timeout)
    
    # 1. Wait for Document Ready State
    wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
    
    # 2. Wait for ASP.NET AJAX (if present)
    # This checks if Sys.WebForms exists and if it's currently in a postback
    aspx_script = """
    return (typeof Sys === 'undefined' || 
            !Sys.WebForms.PageRequestManager.getInstance().get_isInAsyncPostBack());
    """
    wait.until(lambda d: d.execute_script(aspx_script))


def wait_for_preloader(driver, timeout=20):
    """Waits for the 'preloader' overlay to disappear."""
    try:
        # We use a short wait to see if the preloader even appears
        # If it's not there within 1 second, we move on.
        WebDriverWait(driver, 1).until(
            EC.presence_of_element_located((By.CLASS_NAME, "preloader"))
        )
        # If it IS there, we wait for it to be invisible/gone
        WebDriverWait(driver, timeout).until(
            EC.invisibility_of_element_located((By.CLASS_NAME, "preloader"))
        )
    except TimeoutException:
        # If the preloader never showed up, that's fine too.
        pass

def safe_click(driver, element):
    """Attempts a standard click; falls back to JS click if intercepted."""
    try:
        element.click()
    except (ElementClickInterceptedException, Exception):
        driver.execute_script("arguments[0].click();", element)