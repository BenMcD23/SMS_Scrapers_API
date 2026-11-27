"""The programme builder's rules: people and mentions, the checks, the
suggestions and the record a built month becomes."""

from datetime import date, datetime

import pytest

from core import programme_builder as pb
from database.models import (
    Cadet,
    NcoHoliday,
    ProgrammeBankItem,
    ProgrammeDraft,
    ProgrammeDraftNight,
    ProgrammeLessonLink,
    ProgrammeNight,
    ProgrammeRoom,
    ProgrammeSession,
    ProgrammeSlot,
    Staff,
    User,
)

D1, D2 = date(2026, 12, 7), date(2026, 12, 14)


def _roster(db):
    db.add_all([
        Staff(cin=3000, first_name="Sam", last_name="Staff", rank="CI", email="staff@317atc.co.uk"),
        Staff(cin=3001, first_name="Sarah", last_name="Hughes", rank="Sgt", email="s.hughes@example.com"),
        Staff(cin=3002, first_name="No", last_name="Email", rank="CI", email=None),
        Cadet(cin=1, first_name="Nina", last_name="Corporal", rank="Cpl", flight="A", email="nco@317atc.co.uk"),
        Cadet(cin=2, first_name="Carl", last_name="Cadet", rank="Cadet", flight="B", email="cadet2@317atc.co.uk"),
        Cadet(cin=3, first_name="Bo", last_name="Banned", rank="Cadet", flight="B", banned=True),
        *[Cadet(cin=10 + i, first_name=f"C{i}", last_name="X", rank="Cadet", flight="ABC"[i % 3])
          for i in range(12)],
    ])
    db.commit()


def _draft(db, nights=(D1, D2)) -> ProgrammeDraft:
    now = datetime(2026, 11, 20)
    draft = ProgrammeDraft(month="2026-12", created_at=now, updated_at=now)
    for d in nights:
        draft.nights.append(ProgrammeDraftNight(date=d, uniform="No.3", duty_nco="Cpl X"))
    db.add(draft)
    db.commit()
    return draft


def _slot(night, title="Drill", period=1, flights=None, **kw) -> ProgrammeSlot:
    slot = ProgrammeSlot(title=title, category=kw.pop("category", "drill"), period=period,
                         flights=flights if flights is not None else [], people=kw.pop("people", []), **kw)
    night.slots.append(slot)
    return slot


def _issues(db, draft, code=None):
    db.commit()
    db.refresh(draft)
    found = pb.check(draft, pb.build_context(db, draft, today=date(2026, 11, 20)))
    return [i for i in found if code is None or i["code"] == code]


# ── flights, nights and labels ────────────────────────────────────────────────

def test_flights_put_probationary_first_and_default_without_cadets(db):
    assert pb.flights(db) == ["C", "A", "B"]
    db.add_all([Cadet(cin=1, first_name="a", last_name="b", flight="b"),
                Cadet(cin=2, first_name="a", last_name="b", flight="D"),
                Cadet(cin=3, first_name="a", last_name="b", flight="NCO"),
                Cadet(cin=4, first_name="a", last_name="b", flight="C")])
    db.commit()
    # Lower case tidied, NCO flight left out: it isn't a cadet flight.
    assert pb.flights(db) == ["C", "B", "D"]


@pytest.mark.parametrize("flights,label", [
    ([], "All"), (["C", "A", "B"], "All"), (["A"], "A Flight"), (["C"], "C Flight (Probationary)"),
    (["B", "A"], "A and B Flights"), (["C", "B"], "C and B Flights"),
])
def test_who_a_session_is_for_reads_naturally(flights, label):
    assert pb.who_label(flights, ["C", "A", "B"]) == label


@pytest.mark.parametrize("old,expected", [
    # January 2026 back on Wed 14th: it lands mid-January 2027, not on the 1st.
    ([date(2026, 1, d) for d in (14, 16, 21, 23, 28, 30)], {0: 3, 1: 4, 2: 5, 3: 6, 4: 7, 5: 8}),
    # A full month of Mondays onto Wednesdays and Fridays: nearest days, in order.
    ([date(2026, 11, d) for d in (2, 9, 16, 23, 30)], {0: 0, 1: 2, 2: 4, 3: 6, 4: 8}),
    # Late nights leave room for the ones after them.
    ([date(2026, 12, d) for d in (29, 30, 31)], {0: 6, 1: 7, 2: 8}),
    # A Wednesday stays a Wednesday when one is near (4 Nov 2026 is a Wednesday).
    ([date(2026, 11, 4)], {0: 1}),
    # More old nights than new ones: the last are dropped.
    ([date(2026, 12, d) for d in range(1, 12)], {i: i for i in range(9)}),
    ([], {}),
])
def test_old_nights_go_on_the_new_nights_nearest_their_day_of_the_month(old, expected):
    jan_2027 = pb.month_dates("2027-01", pb.PARADE_WEEKDAYS)  # 1, 6, 8, 13, 15, 20, 22, 27, 29
    assert pb.align_nights(old, jan_2027) == expected


def test_a_month_is_every_wednesday_and_friday():
    assert [d.day for d in pb.month_dates("2026-12", pb.PARADE_WEEKDAYS)] == [2, 4, 9, 11, 16, 18, 23, 25, 30]

def test_month_dates_and_bad_months():
    assert pb.month_dates("2026-12", [0]) == [date(2026, 12, d) for d in (7, 14, 21, 28)]
    assert pb.month_bounds("2026-12") == (date(2026, 12, 1), date(2027, 1, 1))
    for bad in ("2026-13", "26-12", "", "2026-1"):
        with pytest.raises(ValueError):
            pb.month_bounds(bad)


# ── people and mentions ───────────────────────────────────────────────────────

def test_directory_names_staff_and_ncos_and_leaves_banned_cadets_out(db):
    _roster(db)
    people = pb.directory(db)
    assert people["staff:3001"].name == "Sgt Sarah Hughes" and people["staff:3001"].kind == "staff"
    assert people["cadet:1"].kind == "nco" and people["cadet:2"].kind == "cadet"
    assert "cadet:3" not in people
    assert people["staff:3001"].mentionable and people["cadet:1"].mentionable
    # A cadet can't open the SMS, and someone with no address can't be emailed.
    assert not people["cadet:2"].mentionable and not people["staff:3002"].mentionable


def test_mentions_keep_real_mentionable_people_once_each(db):
    _roster(db)
    people = pb.directory(db)
    body = ("@[Sgt Sarah Hughes](staff:3001) and @[Nina](cadet:1), again @[Sarah](staff:3001); "
            "@[Carl](cadet:2) is a cadet, @[Ghost](staff:999) isn't anyone, [x](staff:3000) has no @")
    assert pb.parse_mentions(body, people) == ["staff:3001", "cadet:1"]
    assert pb.plain_mentions("Hi @[Sgt Sarah Hughes](staff:3001)!") == "Hi @Sgt Sarah Hughes!"


def test_a_signed_in_account_is_found_on_the_roster_staff_first(db):
    _roster(db)
    db.add(Cadet(cin=50, first_name="Old", last_name="Cadet", email="STAFF@317atc.co.uk"))
    db.commit()
    people = pb.directory(db)
    assert pb.person_for_email(people, "staff@317atc.co.uk").key == "staff:3000"
    assert pb.person_for_email(people, "nobody@x.com") is None and pb.person_for_email(people, None) is None


def test_expected_numbers_round_up_and_assume_three_quarters_with_no_register(db):
    _roster(db)  # 4 cadets in each of A, B, C plus the NCO in A and Carl in B
    expected = pb.expected_numbers(db, ["C", "A", "B"], today=date(2026, 11, 20))
    assert expected == {"C": 3, "A": 4, "B": 4}


# ── checks ────────────────────────────────────────────────────────────────────

def test_someone_on_two_sessions_at_once_is_a_clash_whole_night_included(db):
    _roster(db)
    draft = _draft(db, nights=(D1,))
    night = draft.nights[0]
    a = _slot(night, "Drill", 1, ["A"], people=["staff:3001"])
    b = _slot(night, "Quiz night", None, ["B"], people=["staff:3001"])
    _slot(night, "First aid", 2, ["C"], people=["cadet:1"])
    clashes = _issues(db, draft, "person_double_booked")
    assert len(clashes) == 1 and clashes[0]["severity"] == "clash"
    assert set(clashes[0]["slot_ids"]) == {a.id, b.id}
    assert "Sgt Sarah Hughes" in clashes[0]["message"] and "1st half" in clashes[0]["message"]


def test_a_room_used_twice_is_a_clash_with_a_free_room_offered(db):
    _roster(db)
    db.add_all([ProgrammeRoom(id=1, name="Hall", capacity=80), ProgrammeRoom(id=2, name="Classroom", capacity=20),
                ProgrammeRoom(id=3, name="Cupboard", capacity=2)])
    draft = _draft(db, nights=(D1,))
    _slot(draft.nights[0], "Drill", 1, ["A"], room_id=1)
    second = _slot(draft.nights[0], "Sport", 1, ["B"], room_id=1)
    [clash] = _issues(db, draft, "room_double_booked")
    # The smallest free room that fits B Flight, not the cupboard.
    assert clash["fix"] == {"slot_id": second.id, "room_id": 2, "label": "Move Sport to Classroom"}


def test_a_room_too_small_for_the_flights_is_a_clash_with_a_bigger_one_offered(db):
    _roster(db)
    db.add_all([ProgrammeRoom(id=1, name="Sim room", capacity=6), ProgrammeRoom(id=2, name="Hall", capacity=80)])
    draft = _draft(db, nights=(D1,))
    slot = _slot(draft.nights[0], "Flight Sim", 1, [], room_id=1)  # everyone: about 11
    [issue] = _issues(db, draft, "room_too_small")
    assert "expects about 11" in issue["message"] and "holds 6" in issue["message"]
    assert issue["fix"]["room_id"] == 2 and issue["fix"]["slot_id"] == slot.id


def test_an_nco_on_holiday_is_a_clash_but_a_cancelled_holiday_is_not(db):
    _roster(db)
    user = User(google_id="g1", email="NCO@317atc.co.uk")
    db.add(user)
    db.flush()
    db.add(NcoHoliday(user_id=user.id, date_from=datetime(2026, 12, 6), date_to=datetime(2026, 12, 8),
                      booked_by_name="N", booked_by_email="nco@317atc.co.uk", created_at=datetime.now()))
    db.add(NcoHoliday(user_id=user.id, date_from=datetime(2026, 12, 14), date_to=datetime(2026, 12, 14),
                      booked_by_name="N", booked_by_email="nco@317atc.co.uk", created_at=datetime.now(),
                      cancelled_at=datetime.now(), cancelled_by_name="N", cancelled_by_email="n"))
    draft = _draft(db)
    _slot(draft.nights[0], "Drill", 1, ["A"], people=["cadet:1"])
    _slot(draft.nights[1], "Drill", 1, ["A"], people=["cadet:1"])
    away = _issues(db, draft, "person_away")
    assert [i["night_id"] for i in away] == [draft.nights[0].id]
    assert "Cpl Nina Corporal is on holiday" in away[0]["message"]


def test_gaps_are_per_flight_and_half_but_an_empty_night_says_so_once(db):
    _roster(db)
    draft = _draft(db)
    _slot(draft.nights[0], "Drill", 1, ["C", "A", "B"])
    _slot(draft.nights[0], "Sport", 2, ["A", "B"])
    gaps = _issues(db, draft, "flight_gap")
    assert [g["message"] for g in gaps] == ["C Flight (Probationary) has nothing in the 2nd half on Mon 7 Dec"]
    empty = _issues(db, draft, "empty_night")
    assert [e["night_id"] for e in empty] == [draft.nights[1].id]


def test_a_lesson_before_the_one_it_follows_is_a_warning(db):
    _roster(db)
    draft = _draft(db)
    _slot(draft.nights[0], "Map Reading 2", 1, ["A"], subject_key="acp_32_2", part=2, category="lessons")
    _slot(draft.nights[1], "Map Reading 1", 1, ["A"], subject_key="acp_32_2", part=1, category="lessons")
    [warning] = _issues(db, draft, "lesson_order")
    assert warning["message"] == "A Flight has Map Reading 2 on Mon 7 Dec before lesson 1"


def test_a_lesson_that_follows_one_in_the_record_is_fine(db):
    _roster(db)
    night = ProgrammeNight(date=date(2026, 11, 2), imported_at=datetime.now())
    night.sessions.append(ProgrammeSession(period=1, flight="A", activity="Map Reading One", category="lessons",
                                           raw="x"))
    db.add(night)
    db.add(ProgrammeLessonLink(activity="Map Reading One", subject_key="acp_32_2", part=1, confirmed=True,
                               source="staff", updated_at=datetime.now()))
    draft = _draft(db, nights=(D1,))
    _slot(draft.nights[0], "Map Reading 2", 1, ["A"], subject_key="acp_32_2", part=2)
    assert _issues(db, draft, "lesson_order") == []


def test_missing_details_are_information_and_clashes_come_first(db):
    _roster(db)
    db.add(ProgrammeRoom(id=1, name="Hall", capacity=80))
    draft = _draft(db, nights=(D1,))
    draft.nights[0].duty_nco = None
    draft.nights[0].uniform = " "
    _slot(draft.nights[0], "Drill", 1, [], people=["staff:3001", "staff:999"])
    _slot(draft.nights[0], "Quiz", 1, [], people=["staff:3001"], instructor="SNCOs", room_id=1)
    issues = _issues(db, draft)
    codes = {i["code"] for i in issues}
    assert {"no_duty_nco", "no_uniform", "no_room", "unknown_person", "person_double_booked"} <= codes
    assert "no_instructor" not in codes  # both have someone
    order = [pb.SEVERITY_ORDER[i["severity"]] for i in issues]
    assert order == sorted(order)
    no_room = next(i for i in issues if i["code"] == "no_room")
    # The hall is taken by the quiz in that half, so there's nowhere to offer.
    assert no_room["fix"] is None


def test_the_same_session_twice_in_a_month_for_a_flight_is_pointed_out(db):
    _roster(db)
    draft = _draft(db)
    _slot(draft.nights[0], "Drill", 1, ["A"])
    _slot(draft.nights[1], "drill", 2, [])
    repeated = _issues(db, draft, "repeated")
    assert [r["message"] for r in repeated] == ["A Flight has Drill 2 times this month"]


# ── suggestions ───────────────────────────────────────────────────────────────

def _bank(db, *items):
    for i, (title, category, kw) in enumerate(items, start=1):
        db.add(ProgrammeBankItem(id=i, title=title, category=category, created_at=datetime.now(), **kw))
    db.commit()
    return db.query(ProgrammeBankItem).order_by(ProgrammeBankItem.id).all()


def _suggest(db, draft, bank, **kw):
    db.commit()
    db.refresh(draft)
    return pb.suggest(draft, pb.build_context(db, draft, today=date(2026, 11, 20)), bank, **kw)


def test_the_next_lesson_beats_everything_and_says_why(db):
    _roster(db)
    bank = _bank(db, ("Map Reading 2", "lessons", {"subject_key": "acp_32_2", "part": 2}),
                 ("Volleyball", "sport", {}), ("Drill", "drill", {}),
                 ("Map Reading 3", "lessons", {"subject_key": "acp_32_2", "part": 3}),
                 ("Sports night", "sport", {"whole_night": True}))
    draft = _draft(db)
    _slot(draft.nights[0], "Map Reading 1", 1, ["A"], subject_key="acp_32_2", part=1)
    cell = (draft.nights[1].id, 1, "A")
    [found] = _suggest(db, draft, bank, only=cell)
    titles = [o["title"] for o in found["options"]]
    assert titles[0] == "Map Reading 2" and "Follows lesson 1 on Mon 7 Dec" in found["options"][0]["reason"]
    # Lesson 3 can't come before 2, and a whole-night session doesn't fit a half.
    assert "Map Reading 3" not in titles and "Sports night" not in titles


def test_what_a_flight_already_has_this_month_ranks_last(db):
    _roster(db)
    bank = _bank(db, ("Drill", "drill", {}), ("Volleyball", "sport", {}))
    draft = _draft(db)
    _slot(draft.nights[0], "Drill", 1, ["A"])
    [found] = _suggest(db, draft, bank, only=(draft.nights[1].id, 1, "A"))
    assert [o["title"] for o in found["options"] if o["kind"] == "bank"] == ["Volleyball", "Drill"]
    assert "No sport and fitness for A Flight this month yet" in found["options"][0]["reason"]
    assert "Already on A Flight's programme this month" in found["options"][1]["reason"]


def test_something_not_done_for_months_says_how_long(db):
    _roster(db)
    night = ProgrammeNight(date=date(2026, 8, 3), imported_at=datetime.now())
    night.sessions.append(ProgrammeSession(period=1, flight="B", activity="First Aid", category="first_aid", raw="x"))
    db.add(night)
    bank = _bank(db, ("First Aid", "first_aid", {}))
    draft = _draft(db, nights=(D1,))
    [found] = _suggest(db, draft, bank, only=(draft.nights[0].id, 2, "B"))
    assert "B Flight last did it 18 weeks ago" in found["options"][0]["reason"]


def test_joining_another_flights_session_is_offered_when_the_room_fits(db):
    _roster(db)
    db.add_all([ProgrammeRoom(id=1, name="Hall", capacity=80), ProgrammeRoom(id=2, name="Sim", capacity=5)])
    draft = _draft(db, nights=(D1,))
    _slot(draft.nights[0], "Drill", 1, ["A"], room_id=1)
    _slot(draft.nights[0], "Flight Sim", 1, ["C"], room_id=2)
    [found] = _suggest(db, draft, [], only=(draft.nights[0].id, 1, "B"))
    assert [o["title"] for o in found["options"]] == ["Drill"]  # the sim room can't take B Flight too
    assert found["options"][0]["kind"] == "join" and "Join A Flight" in found["options"][0]["reason"]


def test_full_cells_get_no_suggestions(db):
    _roster(db)
    bank = _bank(db, ("Drill", "drill", {}))
    draft = _draft(db, nights=(D1,))
    _slot(draft.nights[0], "Sport", None, [])
    assert _suggest(db, draft, bank) == []


# ── the record ────────────────────────────────────────────────────────────────

def test_a_built_month_becomes_one_row_per_flight_per_half(db):
    _roster(db)
    draft = _draft(db, nights=(D1,))
    night = draft.nights[0]
    _slot(night, "Quiz", None, [], people=["staff:3001", "cadet:1", "cadet:2"])
    _slot(night, "Band", 1, ["A"], instructor="CI Stone")
    db.commit()
    db.refresh(draft)
    [row] = pb.record_nights(draft, pb.build_context(db, draft, today=date(2026, 11, 20)))
    assert row["date"] == D1 and row["uniform"] == "No.3"
    quiz = [s for s in row["sessions"] if s["activity"] == "Quiz"]
    assert sorted((s["period"], s["flight"]) for s in quiz) == [(p, f) for p in (1, 2) for f in "ABC"]
    # Cadets on a session aren't running it; the staff member and the NCO are.
    assert quiz[0]["instructor"] == "Sgt Sarah Hughes, Cpl Nina Corporal"
    split = {(s["activity"], s["period"], s["flight"]): s["split"] for s in row["sessions"]}
    assert split[("Band", 1, "A")] and split[("Quiz", 1, "A")] and not split[("Quiz", 2, "A")]
    band = next(s for s in row["sessions"] if s["activity"] == "Band")
    assert band["instructor"] == "CI Stone" and band["raw"].startswith("builder:")
