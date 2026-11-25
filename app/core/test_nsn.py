"""Stock numbers: the lookup read from the Logs Form 202 template, and the
numbers staff teach for labels the form doesn't list."""

import pytest

from core import catalogue_store, nsn
from core.catalogue import NO_SIZE_ITEMS, UNIFORM_SIZES
from database.models import StoresNsn
from form_generators.logs_form_gen import DESCRIPTION_TEMPLATES, build_description

MID_BLUE_MALE_110_40 = "8405998456634"  # the bag in the photo the scanner was built from


@pytest.mark.parametrize("raw", ["8405998456634", "8405-99-845-6634", " 8405 99 845 6634 "])
def test_a_stock_number_is_read_however_it_was_written(raw):
    assert nsn.normalise(raw) == MID_BLUE_MALE_110_40


@pytest.mark.parametrize("raw", [None, "", "1", "EACH", "84059984566341", "8405-99-845-663X", "840599845663"])
def test_anything_else_is_not_a_stock_number(raw):
    # The bag's other two barcodes are the quantity ("1") and unit ("EACH").
    assert nsn.normalise(raw) is None


def test_a_number_is_printed_with_its_dashes():
    assert nsn.formatted(MID_BLUE_MALE_110_40) == "8405-99-845-6634"


def test_the_form_gives_the_number_back_with_its_leading_zeros():
    lines = nsn.form_lines()
    assert lines[MID_BLUE_MALE_110_40] == "Shirt Long Sleeve Mid Blue Male Size 110/40"
    assert all(len(k) == 13 and k.isdigit() for k in lines)
    assert len(lines) == 212


def test_every_size_we_demand_on_the_form_is_known_by_its_number():
    # Each catalogue size the logs form can demand must scan back to itself,
    # or booking in that size would ask staff to teach a number the form lists.
    rules = catalogue_store.DEFAULT_RULES
    back = {nsn.catalogue_item(d, rules) for d in nsn.form_lines().values()} - {None}
    for item_type, sizes in {**UNIFORM_SIZES, "Belt": ("",)}.items():
        if build_description(item_type, "x") is None:
            continue
        for size in sizes:
            expected = nsn.NO_SIZE if item_type in NO_SIZE_ITEMS else size
            assert (item_type, expected) in back, (item_type, size)


@pytest.mark.parametrize("description,expected", [
    ("Beret RAF Size 56", ("Beret", "56")),
    ("Shirt Long Sleeve Mid Blue Female 85/33", ("Working Blue Female", "85/33")),
    ("Trousers Woman’s RAF 80/75/99", ("Slacks", "80/75/99")),  # curly quote, as the form has it
    ("Necktie Black (Unisex Item) Short", ("Tie", "N/A")),  # ties are stocked unsized
    ("Belt Waist RAF (Unisex Item) Size 114-130cm", ("Belt", "N/A")),
    ("BRASSARD, Combined Cadet Force (RAF sections) and Air Training Corps", ("Brassard", "N/A")),
    ("Gloves Men’s Tri-Service Black Size 8", None),  # on the form, not a stores item
    ("Beret RAF Size 99", None),  # a size the catalogue doesn't have
])
def test_a_form_description_names_a_catalogue_item_and_size(description, expected):
    assert nsn.catalogue_item(description, catalogue_store.DEFAULT_RULES) == expected


def test_a_retired_or_unknown_item_type_is_not_matched():
    rules = catalogue_store.DEFAULT_RULES
    without_berets = catalogue_store.UniformRules(**{**rules.__dict__, "known": rules.known - {"Beret"}})
    assert nsn.catalogue_item("Beret RAF Size 56", without_berets) is None


def test_lookup_of_a_form_number(db):
    assert nsn.lookup(db, MID_BLUE_MALE_110_40) == {
        "nsn": MID_BLUE_MALE_110_40, "formatted": "8405-99-845-6634",
        "description": "Shirt Long Sleeve Mid Blue Male Size 110/40",
        "itemType": "Working Blue Male", "size": "110/40", "source": "logs_form", "addedBy": None,
    }


def test_lookup_of_a_form_line_the_stores_dont_keep(db):
    gloves = next(k for k, d in nsn.form_lines().items() if d.startswith("Gloves Men"))
    found = nsn.lookup(db, gloves)
    assert found["itemType"] is None and found["source"] == "logs_form"
    assert found["description"].startswith("Gloves Men")


def test_lookup_of_an_unknown_number_is_none(db):
    assert nsn.lookup(db, "8410998696671") is None


def test_a_taught_number_is_looked_up(db):
    nsn.teach(db, "8410998696671", "Working Blue Female", "95/36", "Sam Staff")
    db.commit()
    found = nsn.lookup(db, "8410998696671")
    assert (found["itemType"], found["size"], found["source"], found["addedBy"]) == \
        ("Working Blue Female", "95/36", "added", "Sam Staff")
    assert found["description"] is None


def test_teaching_again_corrects_it_rather_than_adding_a_second(db):
    nsn.teach(db, "8410998696671", "Working Blue Female", "95/36", "Sam")
    nsn.teach(db, "8410998696671", "Working Blue Female", "100/38", "Sue")
    db.commit()
    rows = db.query(StoresNsn).all()
    assert [(r.size, r.added_by) for r in rows] == [("100/38", "Sue")]


def test_a_number_the_form_lists_cant_be_taught_differently(db):
    with pytest.raises(nsn.TeachError, match="logs form already lists 8405-99-845-6634"):
        nsn.teach(db, MID_BLUE_MALE_110_40, "Beret", "56", "Sam")


def test_a_form_line_the_stores_dont_keep_can_be_taught(db):
    # Say the squadron starts stocking gloves under its own catalogue item.
    gloves = next(k for k, d in nsn.form_lines().items() if d.startswith("Gloves Men"))
    nsn.teach(db, gloves, "Polo", "M", "Sam")
    db.commit()
    found = nsn.lookup(db, gloves)
    assert (found["itemType"], found["source"]) == ("Polo", "added")
    assert found["description"].startswith("Gloves Men")


@pytest.mark.parametrize("item_type,size,message", [
    ("Cravat", "M", "Choose an item"),
    ("", "", "Choose an item"),
    ("Beret", "", "Choose one of the Beret sizes"),
    ("Beret", "99", "Choose one of the Beret sizes"),
])
def test_teaching_needs_a_catalogue_item_and_one_of_its_sizes(db, item_type, size, message):
    with pytest.raises(nsn.TeachError, match=message):
        nsn.teach(db, "8410998696671", item_type, size, "Sam")


def test_an_unsized_item_is_taught_as_stock_stores_it(db):
    row = nsn.teach(db, "8410998696671", "Brassard", "whatever", "Sam")
    assert row.size == "N/A"


def test_the_templates_cover_every_item_the_form_demands():
    # A new DESCRIPTION_TEMPLATES entry is matched without a code change here.
    assert {t for t, _ in nsn._description_patterns()} >= set(DESCRIPTION_TEMPLATES)
