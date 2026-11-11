"""Who counts as an NCO."""

import pytest

from core.ranks import is_nco_rank


@pytest.mark.parametrize("rank", [
    "Corporal", "Sergeant", "Flight Sergeant", " flight  sergeant ".replace("  ", " "),
    # The short forms: the preview seed stores these, and an NCO list that
    # silently drops them shows nobody.
    "Cpl", "Sgt", "FS", "F/Sgt",
])
def test_nco_ranks_in_either_spelling(rank):
    assert is_nco_rank(rank)


@pytest.mark.parametrize("rank", [None, "", "Cadet", "CWO", "Cadet Warrant Officer", "Flt Lt"])
def test_cadets_staff_and_blank_ranks_are_not_ncos(rank):
    assert not is_nco_rank(rank)
