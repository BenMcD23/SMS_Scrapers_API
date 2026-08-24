"""The pure parts of the DataTables/WebForms helpers.

These cover the postback fast path — the browser fallback in profiles.py is what
handles anything these get wrong, but a silently wrong postback target would
open the wrong person's profile, so the extraction is pinned here.
"""
from scripts.tables import entries_total, postback_target
from scripts.profiles import link_index


def test_reads_the_target_and_argument_off_a_linkbutton():
    href = "javascript:__doPostBack('ctl00$ctl00$cphBaseBody$cphBody$lvCadets$ctrl12$lbFamilyName','')"
    assert postback_target(href) == (
        "ctl00$ctl00$cphBaseBody$cphBody$lvCadets$ctrl12$lbFamilyName",
        "",
    )


def test_reads_a_non_empty_argument():
    href = "javascript:__doPostBack('ctl00$gv','Sort$Surname')"
    assert postback_target(href) == ("ctl00$gv", "Sort$Surname")


def test_tolerates_double_quotes_and_whitespace():
    href = 'javascript:__doPostBack( "ctl00$lnk" , "" )'
    assert postback_target(href) == ("ctl00$lnk", "")


def test_anything_that_is_not_a_postback_is_none():
    """A real href or a placeholder means click it instead."""
    assert postback_target("#") is None
    assert postback_target("details/detail.aspx?eventId=42") is None
    assert postback_target("") is None
    assert postback_target(None) is None


def test_row_index_comes_off_the_control_id():
    assert link_index("ctl00_ctl00_cphBaseBody_cphBody_lvCadets_ctrl0_lbFamilyName") == 0
    assert link_index("ctl00_ctl00_cphBaseBody_cphBody_lvStaff_ctrl137_lnkFamilyName") == 137
    assert link_index("ctl00_ctl00_cphBaseBody_cphBody_lbBulkAddQuals") is None
    assert link_index(None) is None


def test_entries_total_handles_thousands_separators():
    assert entries_total("Showing 1 to 25 of 2,047 entries") == 2047
    assert entries_total("Showing 1 to 10 of 47 entries") == 47


def test_entries_total_takes_the_filtered_count():
    """The filtered count is the one that actually gets drawn."""
    text = "Showing 1 to 3 of 3 entries (filtered from 212 total entries)"
    assert entries_total(text) == 3


def test_entries_total_is_none_when_the_info_line_is_missing_or_odd():
    assert entries_total("") is None
    assert entries_total(None) is None
    assert entries_total("No matching records found") is None
