from datetime import datetime

from scripts.attendance import _parse_attendance_row, _expected_total


def test_parse_attendance_row():
    row = _parse_attendance_row([
        "27/03/2024", "Parade Night", "Absent", "317 (Failsworth & Newton Heath)",
    ])
    assert row == {
        "date": datetime(2024, 3, 27),
        "register_type": "Parade Night",
        "status": "Absent",
        "unit": "317 (Failsworth & Newton Heath)",
    }

    # Bader wraps every cell in whitespace/newlines
    row = _parse_attendance_row([
        "\n  05/06/2024\n", "\n Parade Night ", " Present Correctly Dressed\n", " 317 \n",
    ])
    assert row["date"] == datetime(2024, 6, 5)
    assert row["status"] == "Present Correctly Dressed"

    # Staff registers may omit the unit column
    row = _parse_attendance_row(["27/03/2024", "Parade Night", "Absent"])
    assert row["date"] == datetime(2024, 3, 27) and row["unit"] is None

    # Rows that aren't real records
    assert _parse_attendance_row([]) is None
    assert _parse_attendance_row(["", "", "", ""]) is None
    assert _parse_attendance_row(["27/03/2024", "Parade Night"]) is None
    assert _parse_attendance_row(["Register Date", "Register Type", "Attendance Status", "Unit"]) is None

    # Day-first, not month-first — 03/04 is 3 April, not 4 March
    assert _parse_attendance_row(["03/04/2024", "a", "b", "c"])["date"] == datetime(2024, 4, 3)

    print("attendance row parse self-check passed")


def test_expected_total():
    assert _expected_total("Showing 1 to 25 of 2,047 entries") == 2047
    assert _expected_total("Showing 1 to 8 of 8 entries") == 8
    assert _expected_total("Showing 0 to 0 of 0 entries (filtered from 12 total entries)") == 0
    assert _expected_total("") is None
    assert _expected_total(None) is None
    print("expected total self-check passed")


if __name__ == "__main__":
    test_parse_attendance_row()
    test_expected_total()
