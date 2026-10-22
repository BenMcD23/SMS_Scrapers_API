"""The query builder behind the OC's custom dashboard.

The OC picks a dataset, a measure, up to two things to group by and some
filters; this turns that into SQL. Everything they can pick is on an
allowlist below, and every value is a bound parameter, so a panel can only
ever read the columns listed here, never names, contact details or medical
data, and can't write anything. Aggregates only: there is no "list the rows"
measure, so a panel never shows who a number is made of.

Written in SQLAlchemy rather than raw SQL so the same code runs on Postgres in
production and SQLite in the tests and the preview; the only dialect-specific
part is bucketing dates (``_date_bucket``).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel
from pydantic import Field as PydField
from sqlalchemy import and_, case, func, literal, literal_column, or_, select, text
from sqlalchemy.orm import Session

from database.models import (
    Cadet,
    CadetAttendance,
    CadetFlight,
    CadetQualification,
    Staff,
    StaffAttendance,
)

# Most groups a panel can come back with; past this the answer is cut and
# flagged rather than shipping thousands of bars to a phone.
MAX_ROWS = 500
# A panel that runs longer than this is cancelled (Postgres only).
STATEMENT_TIMEOUT_MS = 5000

Kind = Literal["text", "date", "number"]
Grain = Literal["day", "week", "month", "year"]


@dataclass(frozen=True)
class FieldDef:
    label: str
    kind: Kind
    column: object  # a SQLAlchemy column expression


@dataclass(frozen=True)
class MeasureDef:
    label: str
    expr: Callable[[], object]
    # Shown after the number ("%", "min"); the site formats with it.
    unit: str = ""


@dataclass(frozen=True)
class Dataset:
    label: str
    description: str
    base: object
    fields: dict[str, FieldDef]
    measures: dict[str, MeasureDef]
    # The date the dashboard's "last N days" picker narrows; None for a
    # dataset that's a snapshot of today (the roster).
    time_field: str | None = None
    joins: tuple = field(default=())


def _attendance_state(status):
    """SQL twin of core.attendance.attendance_state: "Present…" is present, any
    spelling of "authorised" is authorised, anything else absent. A test pins
    the two together."""
    lowered = func.lower(func.coalesce(status, ""))
    return case(
        (func.coalesce(status, "").like("Present%"), literal("present")),
        (or_(lowered.like("%authoris%"), lowered.like("%authoriz%")), literal("authorised")),
        else_=literal("absent"),
    )


def _rate(status):
    """Turnout as a percentage: present rows over all rows."""
    return lambda: func.avg(case((_attendance_state(status) == "present", 100.0), else_=0.0))


def _count():
    return func.count()


DATASETS: dict[str, Dataset] = {
    "cadets": Dataset(
        label="Cadets",
        description="The squadron roster as it stands today, one row per cadet.",
        base=Cadet,
        fields={
            "flight": FieldDef("Flight", "text", Cadet.flight),
            "rank": FieldDef("Rank", "text", Cadet.rank),
            "classification": FieldDef("Classification", "text", Cadet.classification),
            "joined_on": FieldDef("Joined", "date", Cadet.joined_on),
        },
        measures={"count": MeasureDef("Number of cadets", _count)},
    ),
    "cadet_attendance": Dataset(
        label="Cadet attendance",
        description="One row per cadet per register (parade nights, events).",
        base=CadetAttendance,
        joins=((Cadet, CadetAttendance.cadet_id == Cadet.cin),),
        fields={
            "date": FieldDef("Date", "date", CadetAttendance.date),
            "register_type": FieldDef("Register", "text", CadetAttendance.register_type),
            "state": FieldDef("Present / authorised / absent", "text",
                              _attendance_state(CadetAttendance.status)),
            "status": FieldDef("Status (as on Bader)", "text", CadetAttendance.status),
            "flight": FieldDef("Flight", "text", Cadet.flight),
            "rank": FieldDef("Rank", "text", Cadet.rank),
            "classification": FieldDef("Classification", "text", Cadet.classification),
        },
        measures={
            "count": MeasureDef("Number of records", _count),
            "turnout": MeasureDef("Turnout", _rate(CadetAttendance.status), "%"),
            "people": MeasureDef("Different cadets",
                                 lambda: func.count(func.distinct(CadetAttendance.cadet_id))),
        },
        time_field="date",
    ),
    "staff_attendance": Dataset(
        label="Staff attendance",
        description="One row per member of staff per register.",
        base=StaffAttendance,
        joins=((Staff, StaffAttendance.staff_id == Staff.cin),),
        fields={
            "date": FieldDef("Date", "date", StaffAttendance.date),
            "register_type": FieldDef("Register", "text", StaffAttendance.register_type),
            "state": FieldDef("Present / authorised / absent", "text",
                              _attendance_state(StaffAttendance.status)),
            "status": FieldDef("Status (as on Bader)", "text", StaffAttendance.status),
            "rank": FieldDef("Rank", "text", Staff.rank),
        },
        measures={
            "count": MeasureDef("Number of records", _count),
            "turnout": MeasureDef("Turnout", _rate(StaffAttendance.status), "%"),
            "people": MeasureDef("Different staff",
                                 lambda: func.count(func.distinct(StaffAttendance.staff_id))),
        },
        time_field="date",
    ),
    "qualifications": Dataset(
        label="Qualifications",
        description="One row per qualification a cadet holds.",
        base=CadetQualification,
        joins=((Cadet, CadetQualification.cadet_id == Cadet.cin),),
        fields={
            "qual_type": FieldDef("Qualification", "text", CadetQualification.qual_type),
            "level": FieldDef("Level", "text", CadetQualification.status),
            "date_achieved": FieldDef("Achieved", "date", CadetQualification.date_achieved),
            "date_expires": FieldDef("Expires", "date", CadetQualification.date_expires),
            "flight": FieldDef("Flight", "text", Cadet.flight),
            "classification": FieldDef("Classification", "text", Cadet.classification),
        },
        measures={
            "count": MeasureDef("Number held", _count),
            "people": MeasureDef("Different cadets",
                                 lambda: func.count(func.distinct(CadetQualification.cadet_id))),
        },
        time_field="date_achieved",
    ),
    "flying": Dataset(
        label="Flying",
        description="One row per sortie a cadet has flown (from the Volunteer Portal).",
        base=CadetFlight,
        joins=((Cadet, CadetFlight.cadet_id == Cadet.cin),),
        fields={
            "date": FieldDef("Date", "date", CadetFlight.date),
            "activity": FieldDef("Activity", "text", CadetFlight.activity),
            "aircraft": FieldDef("Aircraft", "text", CadetFlight.aircraft),
            "category": FieldDef("Category", "text", CadetFlight.category),
            "unit": FieldDef("Flying unit", "text", CadetFlight.unit),
            "minutes": FieldDef("Minutes", "number", CadetFlight.minutes),
            "flight": FieldDef("Flight", "text", Cadet.flight),
        },
        measures={
            "count": MeasureDef("Number of sorties", _count),
            "minutes": MeasureDef("Total minutes", lambda: func.coalesce(func.sum(CadetFlight.minutes), 0), "min"),
            "avg_minutes": MeasureDef("Average minutes", lambda: func.avg(CadetFlight.minutes), "min"),
            "people": MeasureDef("Different cadets",
                                 lambda: func.count(func.distinct(CadetFlight.cadet_id))),
        },
        time_field="date",
    ),
}


# ── The request ───────────────────────────────────────────────────────────────

FilterOp = Literal["eq", "neq", "in", "not_in", "gt", "gte", "lt", "lte", "empty", "not_empty"]
Scalar = str | int | float


class GroupBy(BaseModel):
    field: str
    # Dates only: what to bucket by. Ignored for text fields.
    grain: Grain = "month"


class Filter(BaseModel):
    field: str
    op: FilterOp
    value: Scalar | list[Scalar] | None = None


class QuerySpec(BaseModel):
    dataset: str
    measure: str = "count"
    group_by: list[GroupBy] = PydField(default_factory=list, max_length=2)
    filters: list[Filter] = PydField(default_factory=list, max_length=10)
    sort: Literal["label", "value_desc", "value_asc"] = "label"
    limit: int = PydField(default=100, ge=1, le=MAX_ROWS)


class QueryError(ValueError):
    """A spec that names something not on the allowlist, or a value that
    doesn't fit its field. The router answers 400 with the message."""


def catalogue() -> list[dict]:
    """What the site's panel editor offers, straight from the allowlist so the
    two can't drift."""
    return [
        {
            "key": key,
            "label": ds.label,
            "description": ds.description,
            "time_field": ds.time_field,
            "fields": [{"key": k, "label": f.label, "kind": f.kind} for k, f in ds.fields.items()],
            "measures": [{"key": k, "label": m.label, "unit": m.unit} for k, m in ds.measures.items()],
        }
        for key, ds in DATASETS.items()
    ]


def validate(spec: QuerySpec) -> Dataset:
    """The dataset, once every name in the spec is checked against it."""
    ds = DATASETS.get(spec.dataset)
    if ds is None:
        raise QueryError(f"Unknown dataset: {spec.dataset}")
    if spec.measure not in ds.measures:
        raise QueryError(f"{ds.label} has no measure '{spec.measure}'")
    for g in spec.group_by:
        if g.field not in ds.fields:
            raise QueryError(f"{ds.label} can't be grouped by '{g.field}'")
    if len({g.field for g in spec.group_by}) != len(spec.group_by):
        raise QueryError("Group by each field only once")
    for f in spec.filters:
        if f.field not in ds.fields:
            raise QueryError(f"{ds.label} can't be filtered by '{f.field}'")
        _filter_clause(ds.fields[f.field], f)  # raises on a value that doesn't fit
    return ds


def _coerce(fd: FieldDef, value: Scalar):
    if fd.kind == "date":
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            raise QueryError(f"'{value}' isn't a date (use YYYY-MM-DD)")
    if fd.kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise QueryError(f"'{value}' isn't a number")
        return value
    return str(value)


def _filter_clause(fd: FieldDef, f: Filter):
    col = fd.column
    if f.op == "empty":
        return or_(col.is_(None), col == "") if fd.kind == "text" else col.is_(None)
    if f.op == "not_empty":
        return and_(col.isnot(None), col != "") if fd.kind == "text" else col.isnot(None)
    if f.op in ("in", "not_in"):
        values = f.value if isinstance(f.value, list) else [f.value]
        values = [_coerce(fd, v) for v in values if v is not None]
        if not values:
            raise QueryError(f"Pick at least one value for '{fd.label}'")
        return col.in_(values) if f.op == "in" else or_(col.is_(None), col.notin_(values))
    if f.value is None or isinstance(f.value, list):
        raise QueryError(f"'{fd.label}' needs a single value")
    value = _coerce(fd, f.value)
    if f.op in ("gt", "gte", "lt", "lte") and fd.kind == "text":
        raise QueryError(f"'{fd.label}' is text, so it can only be matched")
    return {
        "eq": lambda: col == value,
        # "Not A" includes the blanks; SQL's NULL <> 'A' would drop them.
        "neq": lambda: or_(col.is_(None), col != value),
        "gt": lambda: col > value,
        "gte": lambda: col >= value,
        "lt": lambda: col < value,
        "lte": lambda: col <= value,
    }[f.op]()


def _date_bucket(col, grain: Grain, dialect: str):
    """The first day of the bucket as YYYY-MM-DD, so every grain sorts and
    charts the same way. Weeks start on Monday."""
    if dialect == "postgresql":
        return func.to_char(func.date_trunc(grain, col), "YYYY-MM-DD")
    if grain == "day":
        return func.strftime("%Y-%m-%d", col)
    if grain == "week":
        return func.date(col, "-6 days", "weekday 1")
    if grain == "month":
        return func.strftime("%Y-%m-01", col)
    return func.strftime("%Y-01-01", col)


def run(db: Session, spec: QuerySpec, range_days: int | None = None, today: date | None = None) -> dict:
    """Run a validated spec. ``range_days`` narrows the dataset's own date to
    the last N days (the dashboard's picker); a roster dataset ignores it."""
    ds = validate(spec)
    dialect = db.get_bind().dialect.name
    measure = ds.measures[spec.measure]

    groups = []
    for i, g in enumerate(spec.group_by):
        fd = ds.fields[g.field]
        col = _date_bucket(fd.column, g.grain, dialect) if fd.kind == "date" else fd.column
        groups.append(col.label(f"g{i}"))
    value = measure.expr().label("value")

    query = select(*groups, value).select_from(ds.base)
    for model, on in ds.joins:
        query = query.join(model, on)
    for f in spec.filters:
        query = query.where(_filter_clause(ds.fields[f.field], f))
    if range_days and ds.time_field:
        start = datetime.combine((today or date.today()) - timedelta(days=range_days), datetime.min.time())
        query = query.where(ds.fields[ds.time_field].column >= start)
    if groups:
        # By position, not by repeating the expressions: with server-side
        # binding Postgres sees a bucket's parameters ($1 in SELECT, $3 in
        # GROUP BY) as two different expressions and refuses the query.
        # psycopg2 inlines them today; this keeps a driver change safe.
        query = query.group_by(*(literal_column(str(i + 1)) for i in range(len(groups))))
        if spec.sort == "value_desc":
            query = query.order_by(value.desc(), *groups)
        elif spec.sort == "value_asc":
            query = query.order_by(value.asc(), *groups)
        else:
            query = query.order_by(*groups)
        # One extra so we can say the answer was cut short.
        query = query.limit(spec.limit + 1)

    if dialect == "postgresql":
        # SET LOCAL lasts for this transaction only, so the pooled connection
        # goes back without the limit.
        db.execute(text(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}"))
    rows = db.execute(query).all()
    truncated = len(rows) > spec.limit
    rows = rows[: spec.limit]

    def number(v):
        if v is None:
            return None
        v = float(v)
        return int(v) if v.is_integer() else round(v, 2)

    def key(v):
        # Dates come back as text from the bucket; anything else (a number
        # field) is passed through for the site to label.
        return v if v is None or isinstance(v, (str, int, float)) else str(v)

    return {
        "rows": [
            {"keys": [key(r[i]) for i in range(len(groups))], "value": number(r[len(groups)])}
            for r in rows
        ],
        "truncated": truncated,
        "unit": measure.unit,
    }
