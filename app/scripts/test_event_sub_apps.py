"""Reading a parent event's sub-apps, with Playwright faked out."""

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import scripts.event_scraper as ev

PREFIX = "ctl00_ctl00_cphBaseBody_cphBody_rpEvents_"


def _card(ctl, title):
    return (f'<div class="card"><h3 class="card-title"><a>{title}</a></h3>'
            f'<a id="{PREFIX}{ctl}_eventCard_fvEventCard_lbAttendees" class="btn disabled">View</a></div>')


class Button:
    def get_attribute(self, name):
        return "btn disabled"  # stops the walk once the button is found


class Page:
    """A sub-apps page whose cards Bader numbered with a gap, as it does."""

    def __init__(self, ctls, missing=()):
        self.html = "".join(_card(c, f"Sub-app {c}") for c in ctls)
        self.missing, self.asked = set(missing), []

    def goto(self, url):
        pass

    def content(self):
        return self.html

    def wait_for_selector(self, selector, timeout):
        self.asked.append(selector)
        if selector.lstrip("#") in self.missing or f"{PREFIX}" not in selector:
            raise PlaywrightTimeoutError("not there")
        return Button()


def _read(monkeypatch, page):
    monkeypatch.setattr(ev, "wait_for_aspx_load", lambda page: None)
    monkeypatch.setattr(ev, "wait_for_preloader", lambda page: None)
    messages = []
    import threading
    return ev.get_sub_app_attendees(page, "evt-1", messages, threading.Lock()), messages


def test_each_sub_app_is_opened_by_its_own_button_id_when_the_numbering_has_gaps(monkeypatch):
    # The ids used to be rebuilt from the loop index (ctl00, ctl01), so the
    # second card was never found: a 20s wait, then "no cadets attending".
    page = Page(["ctl00", "ctl02"])
    results, messages = _read(monkeypatch, page)
    assert page.asked == [f"#{PREFIX}ctl00_eventCard_fvEventCard_lbAttendees",
                          f"#{PREFIX}ctl02_eventCard_fvEventCard_lbAttendees"]
    assert [r["attendees"] for r in results] == ["No access/Disabled", "No access/Disabled"]
    assert not any("Exception" in m for m in messages)


def test_a_sub_app_that_fails_to_read_says_so_rather_than_no_cadets(monkeypatch):
    page = Page(["ctl02"], missing={f"{PREFIX}ctl02_eventCard_fvEventCard_lbAttendees"})
    results, messages = _read(monkeypatch, page)
    assert results == [{"sub_app_name": "Sub-app ctl02", "attendees": "Could not read attendees."}]
    assert any("Exception for sub-app 'Sub-app ctl02'" in m for m in messages)


def test_an_event_without_sub_apps_reads_as_none(monkeypatch):
    results, _ = _read(monkeypatch, Page([]))
    assert results == []
