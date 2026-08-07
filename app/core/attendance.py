"""How a scraped Bader attendance status maps to present / authorised / absent.

One rule, in one place: it drives the per-person register, the squadron-wide
night summaries, and the monthly counts that prefill the HTD claim. Those must
agree, so nothing re-derives it locally.
"""

PRESENT = "present"
AUTHORISED = "authorised"
ABSENT = "absent"


def attendance_state(status: str | None) -> str:
    """Statuses are scraped verbatim from Bader. Every turned-up state starts
    "Present" ("Present Correctly Dressed", "Present Incorrectly Dressed"); an
    excused non-attendance reads "Authorised Absence".

    Anything unrecognised falls through to ABSENT rather than PRESENT, so a
    status we haven't seen can never be silently counted as attended.
    """
    text = (status or "").strip()
    if text.startswith("Present"):
        return PRESENT
    if "authoris" in text.casefold() or "authoriz" in text.casefold():
        return AUTHORISED
    return ABSENT


def count_states(records) -> dict:
    """{"present": n, "authorised": n, "absent": n} over rows with a `.status`."""
    counts = {PRESENT: 0, AUTHORISED: 0, ABSENT: 0}
    for record in records:
        counts[attendance_state(record.status)] += 1
    return counts
