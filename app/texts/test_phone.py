"""Number reading — the shapes people actually type, and what gets rejected."""

import pytest

from texts.phone import clean_mobile, is_uk_mobile, normalise_phone


@pytest.mark.parametrize("written, expected", [
    ("07700900000", "07700900000"),
    ("07700 900 000", "07700900000"),
    ("07700-900-000", "07700900000"),
    ("(07700) 900000", "07700900000"),
    ("+447700900000", "07700900000"),
    ("+44 7700 900000", "07700900000"),
    ("00447700900000", "07700900000"),
    ("447700900000", "07700900000"),
    # Excel drops the leading zero off a mobile typed as a number.
    ("7700900000", "07700900000"),
    (7700900000.0, "07700900000"),
    (None, ""),
    ("", ""),
])
def test_every_way_of_writing_one_mobile_reads_the_same(written, expected):
    assert normalise_phone(written) == expected


def test_an_unrecognised_number_keeps_its_digits():
    # A landline isn't a mobile, but mangling it would be worse than passing it
    # through — import has always taken numbers it can't classify.
    assert normalise_phone("0161 496 0000") == "01614960000"


@pytest.mark.parametrize("phone, ok", [
    ("07700900000", True),
    ("01614960000", False),   # landline
    ("0770090000", False),    # a digit short
    ("077009000000", False),  # a digit long
    ("", False),
])
def test_only_uk_mobiles_can_be_texted(phone, ok):
    assert is_uk_mobile(phone) is ok


def test_clean_mobile_normalises_before_judging():
    assert clean_mobile("+44 7700 900000") == "07700900000"


def test_clearing_a_number_is_allowed():
    assert clean_mobile("") == ""
    assert clean_mobile("   ") == ""


def test_a_number_notify_cannot_text_is_refused_with_a_readable_reason():
    with pytest.raises(ValueError, match="UK mobile"):
        clean_mobile("0161 496 0000")
