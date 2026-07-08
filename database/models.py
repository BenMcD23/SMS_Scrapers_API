from sqlalchemy import (
    Column, Integer, BigInteger, Float, Boolean, Text, DateTime,
    ForeignKey, LargeBinary, JSON, UniqueConstraint,
)
from sqlalchemy.orm import relationship, backref
from database.database import Base


class Cadet(Base):
    __tablename__ = "Cadets"

    cin         = Column(BigInteger, primary_key=True)  # CIN is the real ID, no autoincrement
    first_name  = Column(Text, nullable=False)
    last_name   = Column(Text, nullable=False)
    email       = Column(Text, nullable=True)

    date_of_birth = Column(DateTime, nullable=True)
    rank          = Column(Text, nullable=True)
    flight        = Column(Text, nullable=True)
    banned        = Column(Boolean, nullable=False, default=False, server_default="0")
    classification = Column(Text, nullable=True)  # highest classification passed, e.g. "Leading Cadet"

    qualifications    = relationship("CadetQualification", back_populates="cadet")
    cadet_events      = relationship("CadetEvent",         back_populates="cadet")
    assessment_sheets = relationship("AssessmentSheet",    back_populates="cadet")
    stores_orders     = relationship("StoresOrder",        back_populates="cadet")
    item_issuances    = relationship("StoresItemIssuance", back_populates="cadet", cascade="all, delete-orphan")
    badge_orders      = relationship("BadgeOrder",         back_populates="cadet")
    medical           = relationship("CadetMedical",       back_populates="cadet", cascade="all, delete-orphan")
    dietary           = relationship("CadetDietary",       back_populates="cadet", cascade="all, delete-orphan")
    theory_progress   = relationship("CadetTheoryProgress", back_populates="cadet", cascade="all, delete-orphan")

class Staff(Base):
    """Squadron staff (CFAV) roster scraped from SMS (staff/default.aspx)."""
    __tablename__ = "Staff"

    cin        = Column(BigInteger, primary_key=True)  # CIN is the real ID
    first_name = Column(Text, nullable=False)
    last_name  = Column(Text, nullable=False)
    rank       = Column(Text, nullable=True)
    email      = Column(Text, nullable=True)
    address    = Column(Text, nullable=True)  # current address from SMS profile
    attendance = Column(JSON, nullable=True)  # {"YYYY-MM": PC+PI} per month this year


QUALIFICATION_TYPES = (
    "duke_of_edinburgh", "first_aid", "leadership", "cyber", "radio",
    "road_marching", "space", "music", "flying_badge", "fieldcraft",
    "shooting", "presentation_skills", "moi", "swimming_proficiency",
    "climatic_injuries"
)

class CadetQualification(Base):
    __tablename__ = "Cadet_Qualifications"

    id      = Column(Integer, primary_key=True, autoincrement=True)
    cadet_id = Column(BigInteger, ForeignKey("Cadets.cin"), nullable=False)
    qual_type = Column(Text, nullable=False)  # one of QUALIFICATION_TYPES
    status   = Column(Text, nullable=False)   # "blue" | "bronze..." | "false, basic, intermediate... - for swimming" | "true/false"
    date_achieved = Column(DateTime, nullable=True)
    date_expires  = Column(DateTime, nullable=True)
    # None = never checked / not on the attachment watch list; set by the
    # cadet-quali scraper for watched quals (see Attachment_Check_Quals).
    has_attachment = Column(Boolean, nullable=True)
    # Set the first time this qualification is included in the 3-month pre-expiry
    # alert email, so each cadet+qualification is only ever notified once.
    expiry_alert_sent_at = Column(DateTime, nullable=True)

    cadet = relationship("Cadet", back_populates="qualifications")


class AttachmentCheckQual(Base):
    """A qualification name (exact Bader text) the cadet-quali scraper should
    check for proof attachments, e.g. "Blue Leadership"."""
    __tablename__ = "Attachment_Check_Quals"

    id        = Column(Integer, primary_key=True, autoincrement=True)
    qual_name = Column(Text, nullable=False, unique=True)


class CadetMedical(Base):
    __tablename__ = "Cadet_Medical"

    id            = Column(Integer,    primary_key=True, autoincrement=True)
    cadet_id      = Column(BigInteger, ForeignKey("Cadets.cin"), nullable=False)
    allergy_name  = Column(Text,       nullable=False)
    auto_injector = Column(Text,       nullable=False, default="No", server_default="No")
    severity      = Column(Text,       nullable=True)
    details       = Column(Text,       nullable=True)

    cadet = relationship("Cadet", back_populates="medical")


class CadetDietary(Base):
    __tablename__ = "Cadet_Dietary"

    id       = Column(Integer,    primary_key=True, autoincrement=True)
    cadet_id = Column(BigInteger, ForeignKey("Cadets.cin"), nullable=False)
    name     = Column(Text,       nullable=False)
    details  = Column(Text,       nullable=True)

    cadet = relationship("Cadet", back_populates="dietary")


class CadetTheoryProgress(Base):
    """Marks that a cadet has completed the *theory* element of a lesson but not
    necessarily the formal assessment/qualification yet — so part-finished
    progress is visible before it lands in the scraped Bader qualifications.

    ``lesson_key`` is one of ``core.theory_lessons.THEORY_LESSONS``. One row per
    cadet+lesson (enforced by the unique constraint); its presence means the
    theory is done.
    """
    __tablename__ = "Cadet_Theory_Progress"

    id           = Column(Integer,    primary_key=True, autoincrement=True)
    cadet_id     = Column(BigInteger, ForeignKey("Cadets.cin", ondelete="CASCADE"), nullable=False)
    lesson_key   = Column(Text,       nullable=False)  # one of core.theory_lessons keys
    completed_at = Column(DateTime,   nullable=False)
    recorded_by  = Column(Text,       nullable=True)   # email of the staff member who marked it

    cadet = relationship("Cadet", back_populates="theory_progress")

    __table_args__ = (
        UniqueConstraint("cadet_id", "lesson_key", name="uq_theory_cadet_lesson"),
    )


class Event317(Base):
    __tablename__ = "317_Events"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    title          = Column(Text, nullable=False)
    reference      = Column(Text, nullable=False)
    adult_ic       = Column(Text, nullable=False)
    contact_number = Column(BigInteger, nullable=False)
    date_from      = Column(DateTime, nullable=False)
    date_to        = Column(DateTime, nullable=False)
    location_id    = Column(Integer, ForeignKey("Location.id"), nullable=False)
    cost           = Column(Float, nullable=False)
    dress          = Column(Text, nullable=False)
    description    = Column(Text, nullable=False)

    location = relationship("Location", back_populates="events")


class Location(Base):
    __tablename__ = "Location"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    first_line = Column(Text, nullable=False)
    postcode   = Column(Text, nullable=False)

    events = relationship("Event317", back_populates="location")


class AllEvent(Base):
    __tablename__ = "All_Events"

    id        = Column(Integer, primary_key=True, autoincrement=True)
    title     = Column(Text, nullable=False)
    parent_id = Column(Integer, ForeignKey("All_Events.id", ondelete="CASCADE"), nullable=True)

    cadet_events = relationship("CadetEvent", back_populates="event")
    sub_apps     = relationship(
        "AllEvent",
        backref=backref("parent", remote_side=[id]),
        foreign_keys=[parent_id],
    )


class CadetEvent(Base):
    __tablename__ = "Cadet_Events"

    id        = Column(Integer, primary_key=True, autoincrement=True)
    event_id  = Column(Integer, ForeignKey("All_Events.id"), nullable=False)
    cadet_id  = Column(BigInteger, ForeignKey("Cadets.cin"), nullable=False)

    cadet = relationship("Cadet", back_populates="cadet_events")
    event = relationship("AllEvent", back_populates="cadet_events")


class BanNotification(Base):
    """One row per (banned cadet, event) we've already emailed staff about, so
    the same pairing is never alerted twice. Keyed on the event *title* rather
    than a Cadet_Events FK because those rows are wiped and recreated on every
    event scrape — the title is the only stable identity across runs."""
    __tablename__ = "Ban_Notifications"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    cadet_id    = Column(BigInteger, ForeignKey("Cadets.cin", ondelete="CASCADE"), nullable=False)
    event_title = Column(Text, nullable=False)
    notified_at = Column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("cadet_id", "event_title", name="uq_ban_notif_cadet_event"),
    )


# ─── Assessment ───────────────────────────────────────────────────────────────

ASSESSMENT_TYPES = ("Blue Leadership", "first_aid", "radio")

class AssessmentSheet(Base):
    """
    One row per completed assessment.

    `fields` stores the assessment-specific scores/results as a dict, e.g.:
        Leadership:  {"command_task": 4, "nav_ex": 3, "overall": "pass"}
        First Aid:   {"scenario_1": "pass", "bandaging": 3, "overall": "pass"}
        Radio:       {"voice_procedure": 4, "net_control": "pass"}

    Adding a new assessment type never requires a schema change.
    """
    __tablename__ = "Assessment_Sheets"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    assessment_type = Column(Text, nullable=False)   # "leadership" | "first_aid" | "radio"
    fields          = Column(JSON, nullable=False, default=dict)
    pdf_data        = Column(LargeBinary, nullable=True)   # generated PDF blob
    pdf_mime_type   = Column(Text, nullable=True, default="application/pdf")
    created_at      = Column(DateTime, nullable=False)
    uploaded        = Column(Boolean, nullable=False, default=False, server_default="0")
    uploaded_at     = Column(DateTime, nullable=True)

    # Optional supporting document (currently only used by MOI — the lesson
    # plan submitted for that lesson). Stored separately from `pdf_data` so
    # the rendered assessment sheet and the lesson plan stay independently
    # editable/replaceable; they're only concatenated at view/upload time.
    lesson_plan_pdf      = Column(LargeBinary, nullable=True)
    lesson_plan_filename = Column(Text, nullable=True)

    cadet_id  = Column(BigInteger, ForeignKey("Cadets.cin"), nullable=False)
    assessor_id = Column(Integer, ForeignKey("Users.id"), nullable=False)  # the user who did it

    cadet    = relationship("Cadet", back_populates="assessment_sheets")
    assessor = relationship("User",  back_populates="assessment_sheets")


# ─── User tables ──────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "Users"

    id        = Column(Integer, primary_key=True, autoincrement=True)
    google_id = Column(Text, unique=True, nullable=False, index=True)
    email     = Column(Text, nullable=False)
    first_name = Column(Text, nullable=True)
    last_name  = Column(Text, nullable=True)

    bader_credentials  = relationship("BaderCredentials", back_populates="user",
                                       uselist=False, cascade="all, delete-orphan")
    signature          = relationship("UserSignature", back_populates="user",
                                       uselist=False, cascade="all, delete-orphan")
    assessment_sheets  = relationship("AssessmentSheet", back_populates="assessor")
    profile            = relationship("UserProfile", back_populates="user",
                                       uselist=False, cascade="all, delete-orphan")
    stores_orders      = relationship("StoresOrder",        back_populates="user")
    item_issuances     = relationship("StoresItemIssuance", back_populates="user", cascade="all, delete-orphan")


class BaderCredentials(Base):
    __tablename__ = "Bader_Credentials"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    user_id          = Column(Integer, ForeignKey("Users.id", ondelete="CASCADE"),
                               unique=True, nullable=False)
    role_username    = Column(Text, nullable=True)
    role_password    = Column(Text, nullable=True)
    personal_username = Column(Text, nullable=True)
    personal_password = Column(Text, nullable=True)

    user = relationship("User", back_populates="bader_credentials")


class UserSignature(Base):
    __tablename__ = "User_Signatures"

    id        = Column(Integer, primary_key=True, autoincrement=True)
    user_id   = Column(Integer, ForeignKey("Users.id", ondelete="CASCADE"),
                        unique=True, nullable=False)
    image_data = Column(LargeBinary, nullable=False)
    mime_type  = Column(Text, nullable=False, default="image/png")

    user = relationship("User", back_populates="signature")

# For F1771e
class UserProfile(Base):
    __tablename__ = "User_Profiles"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    user_id     = Column(Integer, ForeignKey("Users.id", ondelete="CASCADE"),
                          unique=True, nullable=False)

    rank        = Column(Text, nullable=True)
    initials    = Column(Text, nullable=True)
    surname     = Column(Text, nullable=True)
    jpa_number  = Column(Text, nullable=True)
    appointment = Column(Text, nullable=True)
    sqn_vgs_no  = Column(Text, nullable=True)
    wing_ccf    = Column(Text, nullable=True)

    # Editable fields
    home_address  = Column(Text, nullable=True)
    car_reg       = Column(Text, nullable=True)
    assessor_name = Column(Text, nullable=True)

    user = relationship("User", back_populates="profile")


# ─── Scraper Runs ─────────────────────────────────────────────────────────────

class ScraperRun(Base):
    """Records when each scraper last ran and whether it succeeded."""
    __tablename__ = "Scraper_Runs"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    scraper_id = Column(Text, nullable=False)   # e.g. "cadet-quali"
    ran_at     = Column(DateTime, nullable=False)
    success    = Column(Boolean, nullable=False, default=True)
    ran_by     = Column(Text, nullable=True)    # email of triggering user
    logs       = Column(Text, nullable=True)    # newline-joined run log buffer


class ScraperSchedule(Base):
    """Squadron-wide automatic run schedule for one named scraper.

    Scheduled runs use the Bader credentials of `user_id` — whoever last
    saved the schedule.
    """
    __tablename__ = "Scraper_Schedules"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    scraper_id   = Column(Text, nullable=False, unique=True)  # one of the named scrapers
    enabled      = Column(Boolean, nullable=False, default=False, server_default="0")
    days_of_week = Column(Text, nullable=False, default="", server_default="")  # csv: "mon,wed,fri"
    hour         = Column(Integer, nullable=False, default=22, server_default="22")
    minute       = Column(Integer, nullable=False, default=0, server_default="0")
    user_id      = Column(Integer, ForeignKey("Users.id", ondelete="SET NULL"), nullable=True)
    updated_by   = Column(Text, nullable=True)
    updated_at   = Column(DateTime, nullable=True)

    user = relationship("User")


# ─── Stats Snapshots ──────────────────────────────────────────────────────────

class StatsSnapshot(Base):
    """
    Periodic snapshot of squadron-wide stats (cadets, badges, etc.).
    Captured automatically after the cadet-quali scraper runs, or on demand.
    """
    __tablename__ = "Stats_Snapshots"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    captured_at = Column(DateTime, nullable=False)
    data        = Column(JSON, nullable=False)


# ─── Stores tables ────────────────────────────────────────────────────────────

# Maps item type → gender category stored in the DB
ITEM_GENDER_MAP: dict[str, str] = {
    "Jumper":             "unisex",
    "Trousers":           "male",
    "Slacks":             "female",
    "Skirts":             "female",
    "Wedgewood Male":     "male",
    "Wedgewood Female":   "female",
    "Working Blue Male":  "male",
    "Working Blue Female": "female",
    "Beret":              "unisex",
    "Tie":                "unisex",
    "Brassard":           "unisex",
    "Belt":               "unisex",
}


class StoresBox(Base):
    __tablename__ = "Stores_Boxes"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    label          = Column(Text,    nullable=False, unique=True)
    shelf_level    = Column(Integer, nullable=True,  default=1,      server_default='1')
    shelf_position = Column(Integer, nullable=True,  default=0,      server_default='0')
    box_width      = Column(Integer, nullable=True,  default=100,    server_default='100')
    top_end        = Column(Text,    nullable=True,  default='left', server_default='left')

    sections = relationship("StoresSection", back_populates="box", cascade="all, delete-orphan")
    items    = relationship("StoresItem",    back_populates="box", cascade="all, delete-orphan")


class StoresSection(Base):
    __tablename__ = "Stores_Sections"

    id       = Column(Integer, primary_key=True, autoincrement=True)
    box_id   = Column(Integer, ForeignKey("Stores_Boxes.id", ondelete="CASCADE"), nullable=False)
    label         = Column(Text,    nullable=False)
    position      = Column(Integer, nullable=True, default=0,   server_default='0')
    section_row   = Column(Integer, nullable=True, default=0,   server_default='0')
    section_width = Column(Integer, nullable=True, default=100, server_default='100')

    box   = relationship("StoresBox",    back_populates="sections")
    items = relationship("StoresItem",   back_populates="section", cascade="all, delete-orphan")


class StoresItem(Base):
    __tablename__ = "Stores_Items"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    item_type  = Column(Text,    nullable=False)
    size       = Column(Text,    nullable=False)
    quantity   = Column(Integer, nullable=False, default=0)
    gender     = Column(Text,    nullable=False)  # "male" | "female" | "unisex"
    box_id     = Column(Integer, ForeignKey("Stores_Boxes.id",    ondelete="CASCADE"), nullable=False)
    section_id = Column(Integer, ForeignKey("Stores_Sections.id", ondelete="CASCADE"), nullable=False)

    box     = relationship("StoresBox",    back_populates="items")
    section = relationship("StoresSection", back_populates="items")


class StoresOrder(Base):
    __tablename__ = "Stores_Orders"

    id         = Column(Integer,  primary_key=True, autoincrement=True)
    cadet_id   = Column(BigInteger,  ForeignKey("Cadets.cin"), nullable=True)
    user_id    = Column(Integer,  ForeignKey("Users.id"),     nullable=True)
    created_at = Column(DateTime, nullable=False)
    completed  = Column(Boolean,  nullable=False, default=False, server_default="0")

    cadet       = relationship("Cadet",           back_populates="stores_orders")
    user        = relationship("User",            back_populates="stores_orders")
    order_items = relationship("StoresOrderItem", back_populates="order", cascade="all, delete-orphan")


class StoresOrderItem(Base):
    __tablename__ = "Stores_Order_Items"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    order_id        = Column(Integer, ForeignKey("Stores_Orders.id", ondelete="CASCADE"), nullable=False)
    item_type       = Column(Text,    nullable=False)
    size            = Column(Text,    nullable=False, default="")
    need_sizing     = Column(Boolean, nullable=False, default=False)
    sizing_details  = Column(Text,    nullable=False, default="")
    qm_notes        = Column(Text,    nullable=False, default="")  # JSON array of {id, content, timestamp, addedBy}
    given_at         = Column(DateTime, nullable=True)
    given_by         = Column(Text,     nullable=True)
    ready_to_collect = Column(DateTime, nullable=True)

    order = relationship("StoresOrder", back_populates="order_items")


ISSUANCE_ITEM_TYPE_MAP: dict[str, str] = {
    "Wedgewood Male":     "Wedgewood Shirt",
    "Wedgewood Female":   "Wedgewood Shirt",
    "Working Blue Male":  "Working Blue Shirt",
    "Working Blue Female": "Working Blue Shirt",
    "Trousers":           "Slacks/Trousers",
    "Slacks":             "Slacks/Trousers",
    "Skirts":             "Skirt",
    "Beret":              "Beret",
    "Jumper":             "Jumper",
    "Tie":                "Tie",
    "Brassard":           "Brassard",
    "Belt":               "Belt",
}

ISSUANCE_CATEGORIES = [
    "Beret",
    "Wedgewood Shirt",
    "Working Blue Shirt",
    "Jumper",
    "Slacks/Trousers",
    "Skirt",
    "Tie",
    "Brassard",
    "Belt",
]


class StoresItemIssuance(Base):
    __tablename__ = "Stores_Item_Issuances"

    id            = Column(Integer,    primary_key=True, autoincrement=True)
    cadet_id      = Column(BigInteger, ForeignKey("Cadets.cin", ondelete="CASCADE"), nullable=True)
    user_id       = Column(Integer,    ForeignKey("Users.id",   ondelete="CASCADE"), nullable=True)
    item_category = Column(Text,       nullable=False)
    last_given    = Column(DateTime,   nullable=False)
    size_given    = Column(Text,       nullable=True)

    cadet = relationship("Cadet", back_populates="item_issuances")
    user  = relationship("User",  back_populates="item_issuances")


# ─── Badge Orders ─────────────────────────────────────────────────────────────

class BadgeOrder(Base):
    __tablename__ = "Badge_Orders"

    id         = Column(Integer,    primary_key=True, autoincrement=True)
    cadet_id   = Column(BigInteger, ForeignKey("Cadets.cin"), nullable=False)
    created_at = Column(DateTime,   nullable=False)
    completed  = Column(Boolean,    nullable=False, default=False, server_default="0")

    cadet       = relationship("Cadet",          back_populates="badge_orders")
    order_items = relationship("BadgeOrderItem", back_populates="order", cascade="all, delete-orphan")


class BadgeOrderItem(Base):
    __tablename__ = "Badge_Order_Items"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    order_id    = Column(Integer, ForeignKey("Badge_Orders.id", ondelete="CASCADE"), nullable=False)
    badge_name  = Column(Text,    nullable=False)
    replacement = Column(Boolean, nullable=False, default=False, server_default="0")  # replacements carry a £2 fee
    qm_notes    = Column(Text,    nullable=False, default="[]")  # JSON [{id, content, timestamp, addedBy}]
    given_at         = Column(DateTime, nullable=True)
    given_by         = Column(Text,     nullable=True)
    ready_to_collect = Column(DateTime, nullable=True)

    order = relationship("BadgeOrder", back_populates="order_items")


# ─── Supplier order batches (Logs Form / badge order list) ───────────────────
# Entries snapshot item/cadet details at add-time so later order edits or
# deletions can't change a batch that has already been sent to RAFAC.

class LogsForm(Base):
    __tablename__ = "Logs_Forms"

    id         = Column(Integer,  primary_key=True, autoincrement=True)
    created_at = Column(DateTime, nullable=False)
    ordered_at = Column(DateTime, nullable=True)  # null = the current open batch

    entries = relationship("LogsFormEntry", back_populates="form", cascade="all, delete-orphan")


class LogsFormEntry(Base):
    __tablename__ = "Logs_Form_Entries"

    id            = Column(Integer,    primary_key=True, autoincrement=True)
    form_id       = Column(Integer,    ForeignKey("Logs_Forms.id", ondelete="CASCADE"), nullable=False)
    order_item_id = Column(Integer,    ForeignKey("Stores_Order_Items.id", ondelete="SET NULL"), nullable=True, unique=True)
    item_type     = Column(Text,       nullable=False)
    size          = Column(Text,       nullable=False, default="")  # Tie: "Short"/"Standard"
    cadet_name    = Column(Text,       nullable=False)
    cadet_cin     = Column(BigInteger, nullable=True)  # null for staff orders
    created_at    = Column(DateTime,   nullable=False)

    form = relationship("LogsForm", back_populates="entries")


class BadgeOrderList(Base):
    __tablename__ = "Badge_Order_Lists"

    id         = Column(Integer,  primary_key=True, autoincrement=True)
    created_at = Column(DateTime, nullable=False)
    ordered_at = Column(DateTime, nullable=True)  # null = the current open list

    entries = relationship("BadgeOrderListEntry", back_populates="order_list", cascade="all, delete-orphan")


class BadgeOrderListEntry(Base):
    __tablename__ = "Badge_Order_List_Entries"

    id            = Column(Integer,  primary_key=True, autoincrement=True)
    list_id       = Column(Integer,  ForeignKey("Badge_Order_Lists.id", ondelete="CASCADE"), nullable=False)
    order_item_id = Column(Integer,  ForeignKey("Badge_Order_Items.id", ondelete="SET NULL"), nullable=True, unique=True)
    badge_name    = Column(Text,     nullable=False)
    cadet_name    = Column(Text,     nullable=False)
    created_at    = Column(DateTime, nullable=False)

    order_list = relationship("BadgeOrderList", back_populates="entries")


# ─── Parade Night Texts ───────────────────────────────────────────────────────

class SmsRecipient(Base):
    """Someone who receives the parade-night SMS (staff/parents list)."""
    __tablename__ = "Sms_Recipients"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    rank         = Column(Text, nullable=False, default="")
    surname      = Column(Text, nullable=False, default="")
    phone_number = Column(Text, nullable=False)


class ParadeNightMessage(Base):
    """
    One row per parade night (Wed/Fri), generated from the programme doc.

    `*_raw` columns hold the text extracted from the programme table;
    `main_message` / `c_flight_message` are the AI-formatted (and staff-edited)
    versions that actually get sent.
    """
    __tablename__ = "Parade_Night_Messages"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    parade_date      = Column(DateTime, nullable=False, unique=True)
    uniform          = Column(Text, nullable=False, default="")  # AI-formatted/edited, gets sent
    uniform_raw      = Column(Text, nullable=False, default="", server_default="")
    dnco             = Column(Text, nullable=False, default="")
    c_flight_raw     = Column(Text, nullable=False, default="")
    main_body_raw    = Column(Text, nullable=False, default="")
    main_message     = Column(Text, nullable=False, default="")
    c_flight_message = Column(Text, nullable=False, default="")
    status           = Column(Text, nullable=False, default="draft")  # "draft" | "ready" | "sent"
    generated_by     = Column(Text, nullable=True)  # model id that produced the text, e.g. "gemini-3.5-flash"
    generated_at     = Column(DateTime, nullable=False)
    sent_at          = Column(DateTime, nullable=True)
    send_results     = Column(JSON, nullable=True)  # [{phone, status_code, error?}]


# ─── Badge Grid ───────────────────────────────────────────────────────────────

class BadgeGridConfig(Base):
    __tablename__ = "Badge_Grid_Config"

    id       = Column(Integer, primary_key=True, autoincrement=True)
    num_rows = Column(Integer, nullable=False, default=1)
    num_cols = Column(Integer, nullable=False, default=1)


class BadgeGridCell(Base):
    __tablename__ = "Badge_Grid_Cells"

    id    = Column(Integer, primary_key=True, autoincrement=True)
    row   = Column(Integer, nullable=False)
    col   = Column(Integer, nullable=False)
    label = Column(Text,    nullable=True)

    items = relationship("BadgeItem", back_populates="cell", cascade="all, delete-orphan")


class BadgeItem(Base):
    __tablename__ = "Badge_Items"

    id       = Column(Integer, primary_key=True, autoincrement=True)
    cell_id  = Column(Integer, ForeignKey("Badge_Grid_Cells.id", ondelete="CASCADE"), nullable=False)
    name     = Column(Text, nullable=False)
    quantity = Column(Integer, nullable=False, default=1, server_default="1")

    cell = relationship("BadgeGridCell", back_populates="items")
