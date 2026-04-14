from sqlalchemy import (
    Column, Integer, BigInteger, Float, Boolean, Text, DateTime,
    ForeignKey, LargeBinary, JSON,
)
from sqlalchemy.orm import relationship
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
    # classification = Column(Text, nullable=True)

    qualifications    = relationship("CadetQualification", back_populates="cadet")
    cadet_events      = relationship("CadetEvent",         back_populates="cadet")
    assessment_sheets = relationship("AssessmentSheet",    back_populates="cadet")
    stores_orders     = relationship("StoresOrder",        back_populates="cadet")

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

    cadet = relationship("Cadet", back_populates="qualifications")

class Event317(Base):
    __tablename__ = "317_Events"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    title          = Column(Text, nullable=False)
    reference      = Column(Text, nullable=False)
    adult_ic       = Column(Text, nullable=False)
    contact_number = Column(Integer, nullable=False)
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

    id    = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(Text, nullable=False)

    cadet_events = relationship("CadetEvent", back_populates="event")


class CadetEvent(Base):
    __tablename__ = "Cadet_Events"

    id        = Column(Integer, primary_key=True, autoincrement=True)
    event_id  = Column(Integer, ForeignKey("All_Events.id"), nullable=False)
    cadet_id  = Column(BigInteger, ForeignKey("Cadets.cin"), nullable=False)

    cadet = relationship("Cadet", back_populates="cadet_events")
    event = relationship("AllEvent", back_populates="cadet_events")
    ban_notifications = relationship("BanNotification", back_populates="cadet_event")


class BanNotification(Base):
    __tablename__ = "Ban_Notifications"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    cadet_event_id = Column(Integer, ForeignKey("Cadet_Events.id"), nullable=False)
    email_sent     = Column(Boolean, nullable=False)

    cadet_event = relationship("CadetEvent", back_populates="ban_notifications")


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


class UserProfile(Base):
    __tablename__ = "User_Profiles"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    user_id     = Column(Integer, ForeignKey("Users.id", ondelete="CASCADE"),
                          unique=True, nullable=False)

    # Fixed fields (set once, not editable by user)
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
    cadet_id   = Column(BigInteger,  ForeignKey("Cadets.cin"), nullable=False)
    created_at = Column(DateTime, nullable=False)

    cadet       = relationship("Cadet",           back_populates="stores_orders")
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

    order = relationship("StoresOrder", back_populates="order_items")