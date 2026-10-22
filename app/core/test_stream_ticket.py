"""Stream tickets: single-use, short-lived, and nothing else."""

import core.stream_ticket as st


def test_a_ticket_redeems_to_the_email_it_was_issued_to_once():
    ticket = st.issue_ticket("a@317atc.co.uk")
    assert st.redeem_ticket(ticket) == "a@317atc.co.uk"
    assert st.redeem_ticket(ticket) is None  # replay


def test_an_expired_ticket_is_rejected_and_still_consumed(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(st.time, "time", lambda: now[0])
    ticket = st.issue_ticket("a@317atc.co.uk")
    now[0] += st.TTL_SECONDS  # expires_at is exclusive
    assert st.redeem_ticket(ticket) is None
    now[0] -= st.TTL_SECONDS  # even rewinding the clock can't revive it
    assert st.redeem_ticket(ticket) is None


def test_unknown_and_empty_tickets_are_rejected():
    assert st.redeem_ticket("nope") is None
    assert st.redeem_ticket("") is None
    assert st.redeem_ticket(None) is None


def test_issuing_prunes_expired_tickets(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(st.time, "time", lambda: now[0])
    monkeypatch.setattr(st, "_tickets", {})
    st.issue_ticket("old@317atc.co.uk")
    now[0] += st.TTL_SECONDS + 1
    st.issue_ticket("new@317atc.co.uk")
    assert len(st._tickets) == 1


def test_tickets_are_unique():
    assert len({st.issue_ticket("a@317atc.co.uk") for _ in range(500)}) == 500
