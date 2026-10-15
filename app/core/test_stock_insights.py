"""The arithmetic behind the stores insights page: what to order and what sits unused."""

from datetime import datetime

from core.stock_insights import DemandRow, StockRow, build_insights, demand_size

NOW = datetime(2026, 10, 1)
SINCE = datetime(2025, 10, 1)
RECENT = datetime(2026, 6, 1)
OLD = datetime(2024, 1, 1)


def ask(item="Beret", size="56", *, at=RECENT, given=False, waiting=False, need_sizing=False):
    return DemandRow(item, size, need_sizing, at, given, waiting)


def insights(demand=(), stock=(), months=12, cover=3, **kw):
    return build_insights(list(demand), list(stock), since=SINCE, months=months,
                          cover_months=cover, **kw)


def line(result, item, size):
    sizes = next(i for i in result["items"] if i["itemType"] == item)["sizes"]
    return next(r for r in sizes if r["size"] == size)


def test_nothing_at_all_is_an_empty_report_not_an_error():
    result = insights()
    assert result["items"] == [] and result["toOrder"] == [] and result["unused"] == []
    assert result["summary"]["requested"] == 0 and result["summary"]["unitsToOrder"] == 0


def test_requests_are_counted_by_item_and_size_and_the_top_size_is_the_most_asked():
    result = insights([ask(size="56"), ask(size="56", given=True), ask(size="57"), ask("Jumper", "94")])
    beret = result["items"][0]
    assert beret["itemType"] == "Beret" and beret["requested"] == 3 and beret["given"] == 1
    assert beret["topSize"] == "56"
    assert [r["size"] for r in beret["sizes"]] == ["56", "57"]
    assert line(result, "Beret", "56")["requested"] == 2


def test_items_are_ranked_by_how_often_they_are_asked_for():
    result = insights([ask("Jumper", "94")] + [ask("Tie", "")] * 3 + [ask(size="56")] * 2)
    assert [i["itemType"] for i in result["items"]] == ["Tie", "Beret", "Jumper"]


def test_sizes_follow_the_catalogue_order_not_the_alphabet():
    # "100" sorts before "74" as text; the catalogue puts 74 first.
    result = insights([ask("Jumper", "100"), ask("Jumper", "74"), ask("Jumper", "made-up")])
    assert [r["size"] for r in result["items"][0]["sizes"]] == ["74", "100", "made-up"]


def test_requests_before_the_window_do_not_count_as_demand():
    result = insights([ask(at=OLD, given=True)], [StockRow("Beret", "56", 2)])
    assert line(result, "Beret", "56")["requested"] == 0
    assert result["unused"][0]["size"] == "56"


def test_a_cadet_still_waiting_from_before_the_window_still_needs_one():
    result = insights([ask(at=OLD, waiting=True)])
    row = line(result, "Beret", "56")
    assert row["requested"] == 0 and row["waiting"] == 1
    assert row["status"] == "short" and row["toOrder"] == 1


def test_order_suggestion_covers_the_forecast_plus_whoever_is_waiting_less_the_shelf():
    # 12 a year = 1 a month; 3 months' cover = 3, plus 2 waiting = 5, less 1 on the shelf.
    demand = [ask("Jumper", "94", given=True)] * 10 + [ask("Jumper", "94", waiting=True)] * 2
    result = insights(demand, [StockRow("Jumper", "94", 1)])
    row = line(result, "Jumper", "94")
    assert row["monthlyRate"] == 1.0 and row["toOrder"] == 4 and row["status"] == "short"
    assert result["summary"]["unitsToOrder"] == 4 and result["summary"]["linesToOrder"] == 1


def test_a_size_asked_for_once_a_year_is_rare_not_on_the_order_list():
    # A quarter of a beret over 3 months rounds to none.
    result = insights([ask(given=True)], [])
    row = line(result, "Beret", "56")
    assert row["toOrder"] == 0 and row["status"] == "rare"
    assert result["toOrder"] == []


def test_half_an_item_or_more_over_the_cover_period_rounds_up_to_one():
    # 2 a year over 3 months = 0.5.
    result = insights([ask(given=True)] * 2, [])
    row = line(result, "Beret", "56")
    assert row["toOrder"] == 1 and row["status"] == "out"


def test_a_rare_size_with_a_cadet_waiting_is_still_ordered():
    result = insights([ask(waiting=True)], [])
    assert line(result, "Beret", "56")["status"] == "short"


def test_enough_on_the_shelf_is_ok_and_not_on_the_order_list():
    result = insights([ask(given=True)] * 4, [StockRow("Beret", "56", 5)])
    assert line(result, "Beret", "56")["status"] == "ok"
    assert result["toOrder"] == []


def test_some_on_the_shelf_but_not_enough_for_the_cover_period_is_low():
    result = insights([ask(given=True)] * 24, [StockRow("Beret", "56", 2)])
    row = line(result, "Beret", "56")
    assert row["status"] == "low" and row["toOrder"] == 4


def test_stock_in_several_boxes_is_added_together():
    result = insights([], [StockRow("Beret", "56", 2), StockRow("Beret", "56", 3)])
    assert line(result, "Beret", "56")["inStock"] == 5


def test_stock_nobody_asked_for_is_listed_as_unused_biggest_first_with_when_it_last_went():
    result = insights(
        [ask("Jumper", "94")],
        [StockRow("Beret", "56", 2), StockRow("Beret", "60", 7), StockRow("Jumper", "94", 1)],
        last_requested={("Beret", "56"): OLD},
    )
    assert [(r["itemType"], r["size"]) for r in result["unused"]] == [("Beret", "60"), ("Beret", "56")]
    assert result["unused"][1]["lastRequested"] == OLD.isoformat()
    assert result["unused"][0]["lastRequested"] is None
    assert result["summary"]["unusedUnits"] == 9 and result["summary"]["unusedLines"] == 2


def test_empty_and_negative_stock_rows_are_ignored():
    result = insights([], [StockRow("Beret", "56", 0), StockRow("Beret", "57", -2)])
    assert result["items"] == [] and result["unused"] == []


def test_items_still_needing_measuring_count_for_the_item_but_not_any_size():
    result = insights([ask(size="", need_sizing=True, waiting=True), ask(size="", waiting=True)])
    beret = result["items"][0]
    assert beret["requested"] == 2 and beret["waiting"] == 2 and beret["awaitingSizing"] == 2
    assert beret["sizes"] == [] and beret["topSize"] is None
    assert result["toOrder"] == []


def test_one_size_items_are_a_single_line_whatever_size_was_typed():
    result = insights(
        [ask("Tie", ""), ask("Tie", "Short"), ask("Belt", "", need_sizing=True)],
        [StockRow("Tie", "Standard", 3), StockRow("Tie", "Short", 1), StockRow("Belt", "64-114cm", 2)],
    )
    tie = line(result, "Tie", "")
    assert tie["requested"] == 2 and tie["inStock"] == 4
    belt = line(result, "Belt", "")
    assert belt["requested"] == 1 and belt["inStock"] == 2


def test_order_list_puts_cadets_already_waiting_first():
    demand = [ask("Jumper", "100", waiting=True)] + [ask("Jumper", "94", given=True)] * 36
    result = insights(demand, [StockRow("Jumper", "94", 1)])
    assert [r["status"] for r in result["toOrder"]] == ["short", "low"]


def test_non_ascii_and_padded_sizes_line_up_with_the_shelf():
    result = insights([ask("Jumper", " 94 ")], [StockRow("Jumper", "94 ", 1)])
    assert len(result["items"][0]["sizes"]) == 1


def test_demand_size_rule():
    assert demand_size("Belt", "anything", False) == ""
    assert demand_size("Beret", " 56 ", False) == "56"
    assert demand_size("Beret", "56", True) is None
    assert demand_size("Beret", "", False) is None
    assert demand_size("Béret spécial", "M", False) == "M"


def test_berets_belts_brassards_and_ties_are_left_off_the_order_list():
    # Short of all four, with cadets waiting, and still not on the list.
    demand = [ask("Beret", "56", waiting=True), ask("Belt", "", waiting=True),
              ask("Brassard", "", waiting=True), ask("Tie", "", waiting=True),
              ask("Jumper", "94", waiting=True)]
    result = insights(demand, [])
    assert [r["itemType"] for r in result["toOrder"]] == ["Jumper"]
    assert result["summary"]["unitsToOrder"] == 1 and result["summary"]["linesToOrder"] == 1
    assert result["notOnOrderList"] == ["Beret", "Belt", "Brassard", "Tie"]
    # ...but their demand and status still show on the sizes view.
    assert line(result, "Beret", "56")["status"] == "short"
