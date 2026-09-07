"""One reading of a phone number, shared by everything that stores one.

Numbers reach us three ways — typed into the portal, typed into the SMS site,
and pasted out of a spreadsheet — and they have to come out the same either
way, because the recipient list is deduplicated on them.
"""

import re

# Anything a person might type between the digits. Excel also hands us floats
# (07700900000 arrives as 7700900000.0), which `_digits` flattens first.
_SEPARATORS = re.compile(r"[\s()\-.]")


def normalise_phone(value) -> str:
    """A UK number in one shape: 07700900000.

    Handles the +44 / 0044 / bare-44 prefixes and the leading 0 a spreadsheet
    eats off a numeric mobile. Anything it doesn't recognise is returned with
    only its separators stripped — import has always accepted numbers we can't
    classify, and silently mangling one would be worse than passing it through.
    """
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    phone = _SEPARATORS.sub("", str(value or ""))

    if phone.startswith("+44"):
        phone = "0" + phone[3:]
    elif phone.startswith("0044"):
        phone = "0" + phone[4:]
    elif re.fullmatch(r"447\d{9}", phone):
        phone = "0" + phone[2:]
    elif re.fullmatch(r"7\d{9}", phone):
        phone = "0" + phone

    return phone


def is_uk_mobile(phone: str) -> bool:
    """True for a normalised UK mobile — the only thing Notify can text."""
    return bool(re.fullmatch(r"07\d{9}", phone or ""))


def clean_mobile(value) -> str:
    """Normalise a number a person typed, rejecting what Notify can't send to.

    Raises ValueError with the message the user should see. An empty input is
    allowed through as "" — that's how a number is cleared.
    """
    phone = normalise_phone(value)
    if not phone:
        return ""
    if not is_uk_mobile(phone):
        raise ValueError("Enter a UK mobile number, e.g. 07700 900000")
    return phone
