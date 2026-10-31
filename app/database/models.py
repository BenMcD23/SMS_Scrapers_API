from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import backref, relationship

from database.database import Base


class Cadet(Base):
    __tablename__ = "Cadets"

    cin         = Column(BigInteger, primary_key=True)  # CIN is the real ID, no autoincrement
    first_name  = Column(Text, nullable=False)
    last_name   = Column(Text, nullable=False)
    email       = Column(Text, nullable=True)
    # Mobile the cadet gets parade-night texts on. Owned by the cadet — they set
    # it themselves on the portal — so nothing scraped ever overwrites it.
    phone_number = Column(Text, nullable=True)
    # Parents'/guardians' addresses, ";"-separated. The cadet scraper adds
    # Bader's next-of-kin addresses; the OC can add more on the OC dashboard. It
    # only ever adds, so nothing the OC typed is lost.
    parent_emails = Column(Text, nullable=True)

    date_of_birth = Column(DateTime, nullable=True)
    rank          = Column(Text, nullable=True)
    flight        = Column(Text, nullable=True)
    banned        = Column(Boolean, nullable=False, default=False, server_default="0")
    classification = Column(Text, nullable=True)  # highest classification passed, e.g. "Leading Cadet"
    # From the Volunteer Portal sync: when they joined this squadron, and the
    # date each classification was passed ({"Leading Cadet": "2025-07-16", ...}).
    joined_on            = Column(Date, nullable=True)
    classification_dates = Column(JSON, nullable=True)
    # When the quali scraper first ran without finding this cadet on SMS;
    # cleared when they're seen again. Nothing is deleted automatically (a
    # misread page must never cost a cadet their records): the overview flags
    # them and staff remove them (DELETE /cadets/{cin}).
    missing_since        = Column(DateTime, nullable=True)

    qualifications    = relationship("CadetQualification", back_populates="cadet")
    cadet_events      = relationship("CadetEvent",         back_populates="cadet")
    assessment_sheets = relationship("AssessmentSheet",    back_populates="cadet")
    stores_orders     = relationship("StoresOrder",        back_populates="cadet")
    item_issuances    = relationship("StoresItemIssuance", back_populates="cadet", cascade="all, delete-orphan")
    badge_orders      = relationship("BadgeOrder",         back_populates="cadet")
    medical           = relationship("CadetMedical",       back_populates="cadet", cascade="all, delete-orphan")
    dietary           = relationship("CadetDietary",       back_populates="cadet", cascade="all, delete-orphan")
    theory_progress   = relationship("CadetTheoryProgress", back_populates="cadet", cascade="all, delete-orphan")
    theory_lessons    = relationship("CadetTheoryLesson", back_populates="cadet", cascade="all, delete-orphan")
    absences          = relationship("CadetAbsence",         back_populates="cadet", cascade="all, delete-orphan")
    attendance        = relationship("CadetAttendance",      back_populates="cadet", cascade="all, delete-orphan")
    whts              = relationship("CadetWht",             back_populates="cadet", cascade="all, delete-orphan")
    leaving_process   = relationship("CadetLeavingProcess",  back_populates="cadet", uselist=False, cascade="all, delete-orphan")
    appointment_application = relationship("AppointmentApplication", back_populates="cadet", uselist=False,
                                           cascade="all, delete-orphan")
    appraisals        = relationship("NcoAppraisal",          back_populates="cadet", cascade="all, delete-orphan")
    appraisal_reminders = relationship("NcoAppraisalReminder", back_populates="cadet", cascade="all, delete-orphan")
    portal_data       = relationship("CadetPortalData",       back_populates="cadet", cascade="all, delete-orphan")
    flights           = relationship("CadetFlight",           back_populates="cadet", cascade="all, delete-orphan",
                                     order_by="CadetFlight.date.desc()")
    activity_log_enrolment = relationship("ActivityLogEnrolment", back_populates="cadet", uselist=False,
                                          cascade="all, delete-orphan")
    activity_logs     = relationship("ActivityLog", back_populates="cadet", cascade="all, delete-orphan")

class RemovedCadet(Base):
    """A cadet staff removed after they left SMS. The quali scraper skips these
    CINs, so a cadet Bader still lists for a while isn't added straight back."""
    __tablename__ = "Removed_Cadets"

    cin        = Column(BigInteger, primary_key=True)
    first_name = Column(Text, nullable=True)
    last_name  = Column(Text, nullable=True)
    removed_at = Column(DateTime, nullable=False)
    removed_by = Column(Text, nullable=True)


class Staff(Base):
    """Squadron staff (CFAV) roster scraped from SMS (staff/default.aspx)."""
    __tablename__ = "Staff"

    cin        = Column(BigInteger, primary_key=True)  # CIN is the real ID
    first_name = Column(Text, nullable=False)
    last_name  = Column(Text, nullable=False)
    rank       = Column(Text, nullable=True)
    email      = Column(Text, nullable=True)
    address    = Column(Text, nullable=True)  # current address from SMS profile
    # Mobile for parade-night texts, set by the member themselves in Settings.
    # Not scraped — SMS doesn't carry it — so a scraper run never clears it.
    phone_number = Column(Text, nullable=True)
    attendance = Column(JSON, nullable=True)  # {"YYYY-MM": PC+PI} per month this year

    # Row-level register history, separate from the monthly `attendance` counts
    # above (which come from the unit-wide staff attendance report).
    attendance_records = relationship("StaffAttendance", back_populates="staff", cascade="all, delete-orphan")


QUALIFICATION_TYPES = (
    "duke_of_edinburgh", "first_aid", "leadership", "cyber", "radio",
    "road_marching", "space", "music", "flying_badge", "fieldcraft",
    "shooting", "presentation_skills", "moi", "swimming_proficiency",
    "climatic_injuries"
)

class CadetQualification(Base):
    __tablename__ = "Cadet_Qualifications"

    id      = Column(Integer, primary_key=True, autoincrement=True)
    cadet_id = Column(BigInteger, ForeignKey("Cadets.cin", ondelete="CASCADE"), nullable=False)
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

    # One row per qualification: the scraper updates it in place. The unique
    # index also serves every "this cadet's qualifications" lookup.
    __table_args__ = (UniqueConstraint("cadet_id", "qual_type", name="uq_cadet_qualification"),)


class AttachmentCheckQual(Base):
    """A qualification name (exact Bader text) the cadet-quali scraper should
    check for proof attachments, e.g. "Blue Leadership"."""
    __tablename__ = "Attachment_Check_Quals"

    id        = Column(Integer, primary_key=True, autoincrement=True)
    qual_name = Column(Text, nullable=False, unique=True)


class CadetMedical(Base):
    __tablename__ = "Cadet_Medical"

    id            = Column(Integer,    primary_key=True, autoincrement=True)
    cadet_id      = Column(BigInteger, ForeignKey("Cadets.cin", ondelete="CASCADE"), nullable=False, index=True)
    allergy_name  = Column(Text,       nullable=False)
    auto_injector = Column(Text,       nullable=False, default="No", server_default="No")
    severity      = Column(Text,       nullable=True)
    details       = Column(Text,       nullable=True)

    cadet = relationship("Cadet", back_populates="medical")


class CadetDietary(Base):
    __tablename__ = "Cadet_Dietary"

    id       = Column(Integer,    primary_key=True, autoincrement=True)
    cadet_id = Column(BigInteger, ForeignKey("Cadets.cin", ondelete="CASCADE"), nullable=False, index=True)
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


class CadetTheoryLesson(Base):
    """One lesson of a subject that a cadet has had. ``part`` is 1 to the
    subject's ``parts`` in ``core.theory_lessons``.

    Once every part is in, ``core.theory_progress`` writes the subject's
    ``CadetTheoryProgress`` row, so everything that reads "subject taught" (the
    classification day planner, stats) keeps reading that one table.
    ``recorded_by`` is the staff member's email, or ``QR check-in`` when the
    cadet scanned the lesson's code."""
    __tablename__ = "Cadet_Theory_Lessons"

    id          = Column(Integer,    primary_key=True, autoincrement=True)
    cadet_id    = Column(BigInteger, ForeignKey("Cadets.cin", ondelete="CASCADE"), nullable=False)
    lesson_key  = Column(Text,       nullable=False)
    part        = Column(Integer,    nullable=False)
    attended_at = Column(DateTime,   nullable=False)
    recorded_by = Column(Text,       nullable=True)

    cadet = relationship("Cadet", back_populates="theory_lessons")

    __table_args__ = (
        UniqueConstraint("cadet_id", "lesson_key", "part", name="uq_theory_lesson_cadet_part"),
    )


class CadetAbsence(Base):
    """A booked absence scraped from Bader's unit absences page. The scraper
    full-replaces this table each run, so it only ever holds current + future
    absences (the ones Bader still shows). A cadet is "absent" for an inspection
    date D when some row has date_from <= D <= date_to."""
    __tablename__ = "Cadet_Absences"

    id         = Column(Integer,    primary_key=True, autoincrement=True)
    cadet_id   = Column(BigInteger, ForeignKey("Cadets.cin", ondelete="CASCADE"), nullable=False, index=True)
    date_from  = Column(DateTime,   nullable=False)
    date_to    = Column(DateTime,   nullable=False)
    reason     = Column(Text,       nullable=True)
    scraped_at = Column(DateTime,   nullable=False)

    cadet = relationship("Cadet", back_populates="absences")


class CadetAttendance(Base):
    """One row of a cadet's Bader attendance register (Service Record →
    Attendance), scraped by the cadet-quali scraper. Bader's history is
    append-only, so the scraper full-replaces a cadet's rows each run rather
    than merging — no dedup or update logic needed."""
    __tablename__ = "Cadet_Attendance"

    id            = Column(Integer,    primary_key=True, autoincrement=True)
    cadet_id      = Column(BigInteger, ForeignKey("Cadets.cin", ondelete="CASCADE"), nullable=False, index=True)
    date          = Column(DateTime,   nullable=False)
    register_type = Column(Text,       nullable=True)  # e.g. "Parade Night"
    status        = Column(Text,       nullable=True)  # e.g. "Present Correctly Dressed", "Absent"
    unit          = Column(Text,       nullable=True)  # unit attended

    cadet = relationship("Cadet", back_populates="attendance")


class CadetWht(Base):
    """One weapon handling test from the cadet's Bader profile (Qualifications &
    Awards → Weapon Handling Tests), scraped by the cadet-quali scraper. Every
    test is kept, expired ones too, so the audit can show who has lapsed. Like
    attendance, a cadet's rows are full-replaced on each run that reads any."""
    __tablename__ = "Cadet_WHTs"

    id           = Column(Integer,    primary_key=True, autoincrement=True)
    cadet_id     = Column(BigInteger, ForeignKey("Cadets.cin", ondelete="CASCADE"), nullable=False, index=True)
    weapon       = Column(Text,       nullable=False)  # e.g. "L98A2", "Scorpion (single shot)"
    date_passed  = Column(Date,       nullable=False)
    date_expires = Column(Date,       nullable=True)
    awarded_by   = Column(Text,       nullable=True)

    cadet = relationship("Cadet", back_populates="whts")


class CadetPortalData(Base):
    """One Volunteer Portal data set for a cadet (WHTs, flying history, ...),
    exactly as the portal returned it — the import's raw copy. What the squadron
    uses is written into the normal tables from it (flights, theory progress,
    join and classification dates); see routers/volunteer_portal.py."""
    __tablename__ = "Cadet_Portal_Data"
    __table_args__ = (UniqueConstraint("cadet_id", "dataset"),)

    id        = Column(Integer,    primary_key=True, autoincrement=True)
    cadet_id  = Column(BigInteger, ForeignKey("Cadets.cin", ondelete="CASCADE"), nullable=False, index=True)
    dataset   = Column(Text,       nullable=False)  # one of volunteer_portal.DATASETS
    data      = Column(JSON,       nullable=False)
    synced_at = Column(DateTime,   nullable=False)

    cadet = relationship("Cadet", back_populates="portal_data")


class CadetFlight(Base):
    """One flight or simulator session from the cadet's Volunteer Portal flying
    record. Replaced wholesale on each sync that returns a record."""
    __tablename__ = "Cadet_Flights"

    id       = Column(Integer,    primary_key=True, autoincrement=True)
    cadet_id = Column(BigInteger, ForeignKey("Cadets.cin", ondelete="CASCADE"), nullable=False, index=True)
    date     = Column(Date,       nullable=False)
    activity = Column(Text,       nullable=False)  # "powered" | "gliding" | "simulator"
    aircraft = Column(Text,       nullable=True)   # "Tutor", "Viking", "PTT", ...
    category = Column(Text,       nullable=True)   # portal's AEF category: "AEF", "VGS", "PTT", "Military"
    duty     = Column(Text,       nullable=True)   # "Blue ATP", "GIF", "AEF", ...
    sortie   = Column(Text,       nullable=True)
    unit     = Column(Text,       nullable=True)   # flying unit, e.g. "11 AEF (RAF Leeming)"
    minutes  = Column(Integer,    nullable=True)

    cadet = relationship("Cadet", back_populates="flights")


class CadetLeavingProcess(Base):
    """An open leaving process for a lapsed cadet.

    The OC emails the parents from their own mailbox (the site only supplies the
    addresses) and records it here; ``sent_at`` starts the two-week notice
    clock. One open row per cadet — the row's existence *is* the "parents
    notified" state, and deleting it is how the OC cancels when a cadet comes
    back. The status the UI shows (waiting / ready) is derived from ``sent_at``,
    never stored, so a countdown can't go stale.
    """
    __tablename__ = "Cadet_Leaving_Process"

    id        = Column(Integer,    primary_key=True, autoincrement=True)
    cadet_id  = Column(BigInteger, ForeignKey("Cadets.cin", ondelete="CASCADE"),
                       nullable=False, unique=True)
    sent_at   = Column(DateTime,   nullable=False)   # when the OC said they'd sent the query
    sent_to   = Column(Text,       nullable=False)   # parent addresses at that moment
    reply_to  = Column(Text,       nullable=True)    # unused since the site stopped sending the query
    sent_by   = Column(Text,       nullable=True)    # OC email, for the audit trail
    # The adjutant is emailed once to remove the cadet; these record that.
    adj_requested_at = Column(DateTime, nullable=True)
    adj_requested_by = Column(Text,     nullable=True)
    adj_sent_to      = Column(Text,     nullable=True)

    cadet = relationship("Cadet", back_populates="leaving_process")


class AppointmentApplication(Base):
    """A staff cadet or uniformed adult staff application being walked through
    its checklist (``core.appointments`` defines the steps).

    A staff cadet application belongs to a cadet on the roster (one each). An
    adult applicant isn't on SMS until HQ appoints them, so theirs is just a
    name. ``steps`` maps each ticked step's key to ``{"at", "by"}``; an unticked
    step has no key. Completion is derived from it unless staff marked the
    application complete by hand (``completed_at``/``completed_by``): one that
    finished before SMS tracked it, or a step the Wing waived.
    """
    __tablename__ = "Appointment_Applications"

    id             = Column(Integer,    primary_key=True, autoincrement=True)
    kind           = Column(Text,       nullable=False)   # "staff_cadet" | "uniformed"
    cadet_id       = Column(BigInteger, ForeignKey("Cadets.cin", ondelete="CASCADE"),
                            nullable=True, unique=True)
    applicant_name = Column(Text,       nullable=True)    # uniformed only
    route          = Column(Text,       nullable=True)    # uniformed only: "snco" | "cfc"
    notes          = Column(Text,       nullable=True)
    steps          = Column(JSON,       nullable=False, default=dict)
    started_at     = Column(DateTime,   nullable=False)
    started_by     = Column(Text,       nullable=True)
    completed_at   = Column(DateTime,   nullable=True)    # marked complete by hand
    completed_by   = Column(Text,       nullable=True)

    cadet = relationship("Cadet", back_populates="appointment_application")


class StaffAttendance(Base):
    """One row of a staff member's Bader attendance register (Service Record →
    Attendance). Full-replaced per person each run, same as CadetAttendance."""
    __tablename__ = "Staff_Attendance"

    id            = Column(Integer,    primary_key=True, autoincrement=True)
    staff_id      = Column(BigInteger, ForeignKey("Staff.cin", ondelete="CASCADE"), nullable=False, index=True)
    date          = Column(DateTime,   nullable=False)
    register_type = Column(Text,       nullable=True)
    status        = Column(Text,       nullable=True)
    unit          = Column(Text,       nullable=True)

    staff = relationship("Staff", back_populates="attendance_records")


class InspectionSheet(Base):
    """A submitted inspection marking sheet. ``data`` is the full per-cadet JSON
    (score, comments, absent flag, awol flag) as sent by the inspection page."""
    __tablename__ = "Inspection_Sheets"

    id           = Column(Integer,  primary_key=True, autoincrement=True)
    date         = Column(DateTime, nullable=False)  # the parade/inspection date
    submitted_by = Column(Text,     nullable=True)
    submitted_at = Column(DateTime, nullable=False)
    data         = Column(JSON,     nullable=False)


class Event317(Base):
    __tablename__ = "317_Events"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    title          = Column(Text, nullable=False)
    reference      = Column(Text, nullable=False)
    adult_ic       = Column(Text, nullable=False)
    contact_number = Column(BigInteger, nullable=False)
    date_from      = Column(DateTime, nullable=False)
    date_to        = Column(DateTime, nullable=False)
    location_id    = Column(Integer, ForeignKey("Location.id"), nullable=False, index=True)
    cost           = Column(Float, nullable=False)
    dress          = Column(Text, nullable=False)
    description    = Column(Text, nullable=False)
    category       = Column(Text, nullable=True)  # Bader's activity category, e.g. "Road Marching"

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
    parent_id = Column(Integer, ForeignKey("All_Events.id", ondelete="CASCADE"), nullable=True, index=True)
    # Off the event's detail page, so only set on parent events; None when the
    # page couldn't be read this run.
    bader_event_id = Column(Text, nullable=True)
    category       = Column(Text, nullable=True)

    cadet_events = relationship("CadetEvent", back_populates="event")
    sub_apps     = relationship(
        "AllEvent",
        backref=backref("parent", remote_side=[id]),
        foreign_keys=[parent_id],
    )


class CadetEvent(Base):
    __tablename__ = "Cadet_Events"

    id        = Column(Integer, primary_key=True, autoincrement=True)
    event_id  = Column(Integer, ForeignKey("All_Events.id", ondelete="CASCADE"), nullable=False, index=True)
    cadet_id  = Column(BigInteger, ForeignKey("Cadets.cin", ondelete="CASCADE"), nullable=False)

    cadet = relationship("Cadet", back_populates="cadet_events")
    event = relationship("AllEvent", back_populates="cadet_events")

    __table_args__ = (UniqueConstraint("cadet_id", "event_id", name="uq_cadet_event"),)


class FlyingEvent(Base):
    """An AEF or gliding event whose paperwork (TG 21, TG 23, AV Med) is being
    tracked. Unlike All_Events this outlives the scrape: it's keyed on Bader's
    event id and only goes away on its own if nothing was ever ticked off;
    once something has, it stays until staff close it by hand."""
    __tablename__ = "Flying_Events"

    id             = Column(Integer,  primary_key=True, autoincrement=True)
    bader_event_id = Column(Text,     nullable=False, unique=True)
    title          = Column(Text,     nullable=False)
    category       = Column(Text,     nullable=False)
    date_from      = Column(DateTime, nullable=True)
    date_to        = Column(DateTime, nullable=True)
    on_bader       = Column(Boolean,  nullable=False, default=True)  # False once the scrape stops finding it
    created_at     = Column(DateTime, nullable=False)
    last_seen_at   = Column(DateTime, nullable=False)

    cadets = relationship("FlyingPaperwork", back_populates="event", cascade="all, delete-orphan")


class FlyingPaperwork(Base):
    """One cadet's paperwork for one flying event."""
    __tablename__ = "Flying_Paperwork"
    __table_args__ = (UniqueConstraint("flying_event_id", "cadet_id"),)

    id              = Column(Integer,    primary_key=True, autoincrement=True)
    flying_event_id = Column(Integer,    ForeignKey("Flying_Events.id", ondelete="CASCADE"), nullable=False, index=True)
    cadet_id        = Column(BigInteger, ForeignKey("Cadets.cin", ondelete="CASCADE"), nullable=False, index=True)
    on_event        = Column(Boolean,    nullable=False, default=True)  # False once Bader drops them from it
    tg21            = Column(Boolean,    nullable=False, default=False)
    tg23            = Column(Boolean,    nullable=False, default=False)
    av_med          = Column(Boolean,    nullable=False, default=False)
    updated_at      = Column(DateTime,   nullable=True)
    updated_by      = Column(Text,       nullable=True)

    event = relationship("FlyingEvent", back_populates="cadets")
    cadet = relationship("Cadet")


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

    cadet_id  = Column(BigInteger, ForeignKey("Cadets.cin", ondelete="CASCADE"), nullable=False, index=True)
    assessor_id = Column(Integer, ForeignKey("Users.id"), nullable=False, index=True)  # the user who did it

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
    committee_requests = relationship("CommitteeRequest",   back_populates="requester")
    session_plans      = relationship("SessionPlan",        back_populates="author",
                                       cascade="all, delete-orphan")
    nco_holidays       = relationship("NcoHoliday",         back_populates="user",
                                       cascade="all, delete-orphan")
    nco_appraisals     = relationship("NcoAppraisal",       back_populates="author")


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

    # Bank details for committee-request reimbursements. Fernet-encrypted at rest
    # (see core.crypto), like Bader_Credentials — never returned outside the
    # owner's own /settings/user-profile response or the payment email body.
    bank_account_name   = Column(Text, nullable=True)
    bank_sort_code      = Column(Text, nullable=True)
    bank_account_number = Column(Text, nullable=True)

    user = relationship("User", back_populates="profile")


class DashboardLayout(Base):
    """One user's saved arrangement of a dashboard: the section order of the
    OC dashboard ("oc"), or the panels of the OC's custom dashboard
    ("oc-custom"). Per user, so one OC moving things round doesn't reshuffle
    another's; `dashboard` names the page so others can join later."""
    __tablename__ = "Dashboard_Layouts"
    __table_args__ = (
        UniqueConstraint("user_id", "dashboard", name="uq_dashboard_layout_user"),
    )

    id         = Column(Integer,  primary_key=True, autoincrement=True)
    user_id    = Column(Integer,  ForeignKey("Users.id", ondelete="CASCADE"), nullable=False)
    dashboard  = Column(Text,     nullable=False)
    sections   = Column(Text,     nullable=False, default="[]")  # JSON: section ids top first, or {"panels": [...]} for oc-custom
    updated_at = Column(DateTime, nullable=False)


# ─── Committee purchase requests ──────────────────────────────────────────────

# submitted ─(OC approves)→ sent_to_committee ─(committee approves)→ approved
#     │                            │                                    │
#     └─(OC rejects)→ rejected ←───┘(committee rejects)                 │
#                                                                       ▼
#                                              sent_for_payment ─(OC)→ paid
# The requester may withdraw from any non-terminal state → withdrawn.
# "Receipts uploaded" is not a status: it's derived from receipt count while the
# request sits in `approved`.
COMMITTEE_REQUEST_STATUSES = (
    "submitted", "sent_to_committee", "approved", "rejected",
    "sent_for_payment", "paid", "withdrawn",
)


class CommitteeRequest(Base):
    __tablename__ = "Committee_Requests"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    year          = Column(Integer, nullable=False)          # e.g. 2026
    seq           = Column(Integer, nullable=False)          # per-year running number
    reference     = Column(Text, nullable=False, unique=True)  # "CR-2026-003"

    title         = Column(Text, nullable=False)
    justification = Column(Text, nullable=False, default="")
    items         = Column(JSON, nullable=False, default=list)  # [{"description", "cost"}]
    total         = Column(Float, nullable=False, default=0.0)

    status           = Column(Text, nullable=False, default="submitted")
    rejection_reason = Column(Text, nullable=True)

    # Generated once at submission and reused for every email + download.
    pdf_data      = Column(LargeBinary, nullable=True)

    requester_id  = Column(Integer, ForeignKey("Users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Audit trail — who did what, when (emails, since the actor may not be a User row).
    created_at           = Column(DateTime, nullable=False)
    sent_to_committee_at = Column(DateTime, nullable=True)
    sent_to_committee_by = Column(Text, nullable=True)
    decided_at           = Column(DateTime, nullable=True)
    decided_by           = Column(Text, nullable=True)
    sent_for_payment_at  = Column(DateTime, nullable=True)
    paid_at              = Column(DateTime, nullable=True)
    paid_marked_by       = Column(Text, nullable=True)
    withdrawn_at         = Column(DateTime, nullable=True)
    withdrawn_by         = Column(Text, nullable=True)

    requester = relationship("User", back_populates="committee_requests")
    receipts  = relationship(
        "CommitteeRequestReceipt", back_populates="request",
        cascade="all, delete-orphan", order_by="CommitteeRequestReceipt.uploaded_at",
    )

    __table_args__ = (
        UniqueConstraint("year", "seq", name="uq_committee_request_year_seq"),
    )


class CommitteeRequestReceipt(Base):
    __tablename__ = "Committee_Request_Receipts"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    request_id  = Column(Integer, ForeignKey("Committee_Requests.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    filename    = Column(Text, nullable=False)
    mime_type   = Column(Text, nullable=False)
    data        = Column(LargeBinary, nullable=False)
    uploaded_at = Column(DateTime, nullable=False)
    uploaded_by = Column(Text, nullable=True)

    request = relationship("CommitteeRequest", back_populates="receipts")


# ─── NCO session plans ────────────────────────────────────────────────────────

# draft ─(NCO submits)→ submitted ─(staff approves)→ approved
#   ▲                        │
#   │                        └─(staff requests changes)→ amendments_requested
#   └──────────────────────────────────(NCO edits and resubmits)──────┘
# `approved` is terminal — the plan is locked once staff sign it off.
SESSION_PLAN_STATUSES = ("draft", "submitted", "amendments_requested", "approved")

# The five parts of the "Section Plan" table on the paper session plan. Staff
# leave a comment against each one (the sheet's "Staff Check & Feedback" column),
# so the keys are shared between the model, the API and the review UI.
SESSION_PLAN_SECTIONS = (
    "situation", "mission", "execution", "any_questions", "check_understanding",
)


class SessionPlan(Base):
    """An NCO's plan for a session they're running, submitted to staff for
    approval. Mirrors the squadron's paper session plan: a header block, the
    five-part section plan, and an optional timetable/map."""
    __tablename__ = "Session_Plans"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Header block
    session_name = Column(Text,     nullable=False)
    session_ic   = Column(Text,     nullable=False, default="", server_default="")
    session_date = Column(DateTime, nullable=True)
    aim          = Column(Text,     nullable=False, default="", server_default="")
    participants = Column(Text,     nullable=False, default="", server_default="")  # rough numbers, free text
    rooming      = Column(Text,     nullable=False, default="", server_default="")
    equipment    = Column(Text,     nullable=False, default="", server_default="")

    # Section plan — one column per SESSION_PLAN_SECTIONS key
    situation           = Column(Text, nullable=False, default="", server_default="")
    mission             = Column(Text, nullable=False, default="", server_default="")
    execution           = Column(Text, nullable=False, default="", server_default="")
    any_questions       = Column(Text, nullable=False, default="", server_default="")
    check_understanding = Column(Text, nullable=False, default="", server_default="")

    # Extras
    timetable = Column(JSON, nullable=False, default=list)  # [{"timing", "event", "location"}]
    notes     = Column(Text, nullable=False, default="", server_default="")

    status = Column(Text, nullable=False, default="draft", server_default="draft")

    # Staff review. `feedback` is keyed by SESSION_PLAN_SECTIONS so a comment
    # sits next to the section it's about; `amendment_note` is the overall
    # "what to change" message sent back to the NCO.
    feedback       = Column(JSON, nullable=False, default=dict)
    amendment_note = Column(Text, nullable=True)

    author_id = Column(Integer, ForeignKey("Users.id", ondelete="CASCADE"), nullable=False, index=True)

    created_at   = Column(DateTime, nullable=False)
    updated_at   = Column(DateTime, nullable=False)
    submitted_at = Column(DateTime, nullable=True)
    reviewed_at  = Column(DateTime, nullable=True)
    reviewed_by  = Column(Text,     nullable=True)  # email of the staff member who decided

    author      = relationship("User", back_populates="session_plans")
    attachments = relationship(
        "SessionPlanAttachment", back_populates="plan",
        cascade="all, delete-orphan", order_by="SessionPlanAttachment.uploaded_at",
    )
    comments = relationship(
        "SessionPlanComment", back_populates="plan",
        cascade="all, delete-orphan", order_by="SessionPlanComment.created_at",
    )


class SessionPlanAttachment(Base):
    """A supporting file on a session plan — usually the map the paper sheet
    leaves space for, but any image or PDF the NCO wants staff to see."""
    __tablename__ = "Session_Plan_Attachments"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    plan_id     = Column(Integer, ForeignKey("Session_Plans.id", ondelete="CASCADE"), nullable=False, index=True)
    filename    = Column(Text,       nullable=False)
    mime_type   = Column(Text,       nullable=False)
    data        = Column(LargeBinary, nullable=False)
    uploaded_at = Column(DateTime,   nullable=False)

    plan = relationship("SessionPlan", back_populates="attachments")


class NcoHoliday(Base):
    """A holiday an NCO books for themselves, mirrored onto the squadron's
    shared "NCO Holidays" Google Calendar as an all-day event.

    This table is the audit trail, so nothing is ever deleted from it —
    cancelling a holiday clears the calendar entry and stamps
    ``cancelled_at``/``cancelled_by_*``, leaving the original booking (and the
    date it was added) on record. Names and emails are snapshotted rather than
    only joined through ``user_id`` so a cancelled row still reads correctly
    once someone's account or name changes."""
    __tablename__ = "NCO_Holidays"

    id      = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("Users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Inclusive whole-day range — a one-day holiday has date_from == date_to.
    date_from = Column(DateTime, nullable=False)
    date_to   = Column(DateTime, nullable=False)
    reason    = Column(Text, nullable=False, default="", server_default="")

    booked_by_name  = Column(Text, nullable=False)
    booked_by_email = Column(Text, nullable=False)

    # Null when the booking saved but Google Calendar couldn't be reached —
    # the holiday is still real, it just isn't on the calendar yet.
    google_event_id = Column(Text, nullable=True)

    created_at = Column(DateTime, nullable=False)  # when the NCO added it

    cancelled_at       = Column(DateTime, nullable=True)
    cancelled_by_name  = Column(Text,     nullable=True)
    cancelled_by_email = Column(Text,     nullable=True)

    user = relationship("User", back_populates="nco_holidays")


class SessionPlanComment(Base):
    """A note anyone who can see the plan can leave on it. Distinct from the
    staff `feedback`/`amendment_note`, which are the approval decision — these
    are just NCOs and staff talking about the plan."""
    __tablename__ = "Session_Plan_Comments"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    plan_id    = Column(Integer, ForeignKey("Session_Plans.id", ondelete="CASCADE"), nullable=False, index=True)
    author_id  = Column(Integer, ForeignKey("Users.id", ondelete="CASCADE"), nullable=False, index=True)
    body       = Column(Text,     nullable=False)
    created_at = Column(DateTime, nullable=False)

    plan   = relationship("SessionPlan", back_populates="comments")
    author = relationship("User")


# ─── NCO appraisals ───────────────────────────────────────────────────────────

# How far ahead the next review can be booked, in months. The appraisal form
# offers exactly these, and `next_review_date` is derived from the choice so the
# stored date and the printed "Next NCO Review" line can never disagree.
NEXT_REVIEW_MONTHS = (3, 6, 12)

# The five free-text sections of the appraisal, in the order they appear on the
# form and in the Word template. Shared by the model, the API, the document
# builders and the AI writer so a section is never named in two places.
APPRAISAL_SECTIONS = (
    "general_observations", "effectiveness_in_role", "strengths", "weaknesses",
    "targets",
)


class NcoAppraisal(Base):
    """A staff appraisal of one cadet NCO, mirroring the squadron's paper form.

    Written by staff, saved here, and printable as the Word template or a PDF.
    The header block (name/age/attendance) is *snapshotted* onto the row rather
    than recomputed on export: an appraisal is a record of what was true on the
    day, so re-opening one next year must not silently age the NCO up or move
    their attendance figure.
    """
    __tablename__ = "NCO_Appraisals"

    id        = Column(Integer,    primary_key=True, autoincrement=True)
    cadet_id  = Column(BigInteger, ForeignKey("Cadets.cin", ondelete="CASCADE"),
                       nullable=False, index=True)
    # Nulled rather than cascaded if the staff account goes — the appraisal
    # itself outlives whoever typed it.
    author_id = Column(Integer,    ForeignKey("Users.id", ondelete="SET NULL"), nullable=True, index=True)

    appraisal_date = Column(DateTime, nullable=False)

    # Snapshot of the template's header row, as printed.
    nco_name   = Column(Text, nullable=False, default="", server_default="")
    age        = Column(Text, nullable=False, default="", server_default="")
    attendance = Column(Text, nullable=False, default="", server_default="")

    # One column per APPRAISAL_SECTIONS key.
    general_observations  = Column(Text, nullable=False, default="", server_default="")
    effectiveness_in_role = Column(Text, nullable=False, default="", server_default="")
    strengths             = Column(Text, nullable=False, default="", server_default="")
    weaknesses            = Column(Text, nullable=False, default="", server_default="")
    targets               = Column(Text, nullable=False, default="", server_default="")

    # Footer row of the form.
    next_review_months = Column(Integer,  nullable=False, default=12, server_default="12")
    next_review_date   = Column(DateTime, nullable=False)
    cause_for_concern  = Column(Boolean,  nullable=False, default=False, server_default="0")
    extend_probation   = Column(Boolean,  nullable=False, default=False, server_default="0")

    # Model id when the sections were drafted by the AI writer, so the UI can
    # say which model wrote it (and flag a fallback). Null when hand-written.
    generated_by = Column(Text, nullable=True)

    # Set once the PDF has been emailed to the NCO — staff can re-send, and the
    # last send is what's shown.
    emailed_at = Column(DateTime, nullable=True)
    emailed_to = Column(Text,     nullable=True)

    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)

    cadet  = relationship("Cadet", back_populates="appraisals")
    author = relationship("User",  back_populates="nco_appraisals")


class NcoAppraisalReminder(Base):
    """A "chase this one up" note for an NCO who has no appraisal yet.

    Everyone with an appraisal already has a next-review date, so they show up
    on the upcoming list on their own. This table covers the gap: an NCO who has
    never been appraised has nothing to sort by until staff put a date on them.

    Deleted — not archived — once an appraisal is saved for that cadet: the
    reminder's whole purpose was to get one written, and the appraisal it
    produced is the record worth keeping.
    """
    __tablename__ = "NCO_Appraisal_Reminders"

    id       = Column(Integer,    primary_key=True, autoincrement=True)
    cadet_id = Column(BigInteger, ForeignKey("Cadets.cin", ondelete="CASCADE"),
                      nullable=False, index=True)

    due_date = Column(DateTime, nullable=False)
    note     = Column(Text,     nullable=False, default="", server_default="")

    created_by_name  = Column(Text,     nullable=False)
    created_by_email = Column(Text,     nullable=False)
    created_at       = Column(DateTime, nullable=False)

    cadet = relationship("Cadet", back_populates="appraisal_reminders")


# ─── NCO quick comments ───────────────────────────────────────────────────────

class NcoComment(Base):
    """A short note an NCO (or staff member) drops about a cadet or about the
    squadron in general — the quick, in-the-moment observations that otherwise
    only ever get said out loud. Everyone who can see the page can reply, so a
    comment becomes a thread.

    `cadet_id` is nulled rather than cascaded when a cadet leaves (the quali
    scraper hard-deletes departed cadets), and `cadet_name` is snapshotted, so
    the note still reads correctly afterwards."""
    __tablename__ = "NCO_Comments"

    id        = Column(Integer, primary_key=True, autoincrement=True)
    author_id = Column(Integer, ForeignKey("Users.id", ondelete="CASCADE"), nullable=False, index=True)

    subject      = Column(Text,     nullable=False)
    body         = Column(Text,     nullable=False)
    comment_date = Column(DateTime, nullable=False)  # the day it's about, prefilled with today

    # Null on a general comment — that's what makes it general.
    cadet_id   = Column(BigInteger, ForeignKey("Cadets.cin", ondelete="SET NULL"), nullable=True, index=True)
    cadet_name = Column(Text, nullable=False, default="", server_default="")

    created_at = Column(DateTime, nullable=False)

    author  = relationship("User")
    cadet   = relationship("Cadet")
    replies = relationship(
        "NcoCommentReply", back_populates="comment",
        cascade="all, delete-orphan", order_by="NcoCommentReply.created_at",
    )


class NcoCommentReply(Base):
    """A reply on an NcoComment's chain."""
    __tablename__ = "NCO_Comment_Replies"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    comment_id = Column(Integer, ForeignKey("NCO_Comments.id", ondelete="CASCADE"), nullable=False, index=True)
    author_id  = Column(Integer, ForeignKey("Users.id", ondelete="CASCADE"), nullable=False, index=True)
    body       = Column(Text,     nullable=False)
    created_at = Column(DateTime, nullable=False)

    comment = relationship("NcoComment", back_populates="replies")
    author  = relationship("User")


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
    user_id      = Column(Integer, ForeignKey("Users.id", ondelete="SET NULL"), nullable=True, index=True)
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


class CadetSnapshot(Base):
    """One cadet as they stood in a stats snapshot: the per-cadet history behind
    drill-downs into the past, intake retention and time between classifications.

    Deliberately no foreign key to Cadets: departed cadets are deleted from
    there, and their history is exactly what retention needs to keep.
    """
    __tablename__ = "Cadet_Snapshots"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_id    = Column(Integer, ForeignKey("Stats_Snapshots.id", ondelete="CASCADE"), nullable=False, index=True)
    cin            = Column(BigInteger, nullable=False, index=True)
    name           = Column(Text, nullable=False)
    flight         = Column(Text, nullable=True)
    rank           = Column(Text, nullable=True)
    classification = Column(Text, nullable=True)
    junior         = Column(Boolean, nullable=False)
    # Dashboard badge key -> held level label ("Bronze"); badges not held are absent.
    badges         = Column(JSON, nullable=False)


class StatsTarget(Base):
    """A squadron goal for a dashboard badge, e.g. 80% of non-juniors holding
    Blue or better First Aid by 1 July. Staff set them; the stats page charts
    progress and projects when the current trend gets there."""
    __tablename__ = "Stats_Targets"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    badge      = Column(Text, nullable=False)         # dashboard badge key, e.g. "first_aid"
    min_level  = Column(Text, nullable=True)          # "Blue" = Blue or better; None = any level
    flight     = Column(Text, nullable=True)          # None = whole squadron
    exclude_juniors = Column(Boolean, nullable=False, default=False, server_default="0")
    target_pct = Column(Integer, nullable=False)      # 1-100
    due        = Column(Date, nullable=False)
    created_by = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False)


# ─── Stores tables ────────────────────────────────────────────────────────────

# Maps item type → gender category stored in the DB
ITEM_GENDER_MAP: dict[str, str] = {
    "Jumper":             "unisex",
    "Trousers":           "male",
    "Slacks":             "female",
    "Skirt":             "female",
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
    box_id   = Column(Integer, ForeignKey("Stores_Boxes.id", ondelete="CASCADE"), nullable=False, index=True)
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
    box_id     = Column(Integer, ForeignKey("Stores_Boxes.id",    ondelete="CASCADE"), nullable=False, index=True)
    section_id = Column(Integer, ForeignKey("Stores_Sections.id", ondelete="CASCADE"), nullable=False, index=True)

    box     = relationship("StoresBox",    back_populates="items")
    section = relationship("StoresSection", back_populates="items")


class StoresOrder(Base):
    __tablename__ = "Stores_Orders"

    id         = Column(Integer,  primary_key=True, autoincrement=True)
    cadet_id   = Column(BigInteger,  ForeignKey("Cadets.cin", ondelete="SET NULL"), nullable=True, index=True)
    user_id    = Column(Integer,  ForeignKey("Users.id"),     nullable=True, index=True)
    created_at = Column(DateTime, nullable=False)
    completed  = Column(Boolean,  nullable=False, default=False, server_default="0")
    kitting    = Column(Boolean,  nullable=False, default=False, server_default="0")

    cadet       = relationship("Cadet",           back_populates="stores_orders")
    user        = relationship("User",            back_populates="stores_orders")
    order_items = relationship("StoresOrderItem", back_populates="order", cascade="all, delete-orphan")


class StoresOrderItem(Base):
    __tablename__ = "Stores_Order_Items"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    order_id        = Column(Integer, ForeignKey("Stores_Orders.id", ondelete="CASCADE"), nullable=False, index=True)
    item_type       = Column(Text,    nullable=False)
    size            = Column(Text,    nullable=False, default="")
    need_sizing     = Column(Boolean, nullable=False, default=False)
    sizing_details  = Column(Text,    nullable=False, default="")
    qm_notes        = Column(Text,    nullable=False, default="")  # JSON array of {id, content, timestamp, addedBy}
    given_at         = Column(DateTime, nullable=True)
    given_by         = Column(Text,     nullable=True)
    ready_to_collect = Column(DateTime, nullable=True)
    # Append-only log of stock removals/returns for this item, so the QM can see
    # who took the item off the shelf and when: JSON [{id, action, timestamp, by, ...}]
    stock_events     = Column(Text, nullable=False, default="[]", server_default="[]")
    # Append-only "given"/"undone" log (core/given_events.py); given_at/given_by above are the current state.
    given_events     = Column(Text, nullable=False, default="[]", server_default="[]")

    order = relationship("StoresOrder", back_populates="order_items")


ISSUANCE_ITEM_TYPE_MAP: dict[str, str] = {
    "Wedgewood Male":     "Wedgewood Shirt",
    "Wedgewood Female":   "Wedgewood Shirt",
    "Working Blue Male":  "Working Blue Shirt",
    "Working Blue Female": "Working Blue Shirt",
    "Trousers":           "Slacks/Trousers",
    "Slacks":             "Slacks/Trousers",
    "Skirt":             "Skirt",
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
    cadet_id      = Column(BigInteger, ForeignKey("Cadets.cin", ondelete="CASCADE"), nullable=True, index=True)
    user_id       = Column(Integer,    ForeignKey("Users.id",   ondelete="CASCADE"), nullable=True, index=True)
    item_category = Column(Text,       nullable=False)
    last_given    = Column(DateTime,   nullable=False)
    size_given    = Column(Text,       nullable=True)

    cadet = relationship("Cadet", back_populates="item_issuances")
    user  = relationship("User",  back_populates="item_issuances")


# ─── Badge Orders ─────────────────────────────────────────────────────────────

class BadgeOrder(Base):
    __tablename__ = "Badge_Orders"

    id         = Column(Integer,    primary_key=True, autoincrement=True)
    cadet_id   = Column(BigInteger, ForeignKey("Cadets.cin", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime,   nullable=False)
    completed  = Column(Boolean,    nullable=False, default=False, server_default="0")

    cadet       = relationship("Cadet",          back_populates="badge_orders")
    order_items = relationship("BadgeOrderItem", back_populates="order", cascade="all, delete-orphan")


class BadgeOrderItem(Base):
    __tablename__ = "Badge_Order_Items"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    order_id    = Column(Integer, ForeignKey("Badge_Orders.id", ondelete="CASCADE"), nullable=False, index=True)
    badge_name  = Column(Text,    nullable=False)
    replacement = Column(Boolean, nullable=False, default=False, server_default="0")  # replacements carry a £2 fee
    qm_notes    = Column(Text,    nullable=False, default="[]")  # JSON [{id, content, timestamp, addedBy}]
    given_at         = Column(DateTime, nullable=True)
    given_by         = Column(Text,     nullable=True)
    ready_to_collect = Column(DateTime, nullable=True)
    # See StoresOrderItem.stock_events — same append-only log, against the badge grid
    stock_events     = Column(Text, nullable=False, default="[]", server_default="[]")
    # Append-only "given"/"undone" log (core/given_events.py); given_at/given_by above are the current state.
    given_events     = Column(Text, nullable=False, default="[]", server_default="[]")
    # Where the badge was gained: "camp" | "sector_training_weekend" | "wing_training_weekend" | "on_sqn" | "other"
    gained_where        = Column(Text,     nullable=True)
    gained_where_detail = Column(Text,     nullable=True)  # free text when gained_where == "other"
    gained_date_from    = Column(DateTime, nullable=True)  # camp / sector training weekend dates
    gained_date_to      = Column(DateTime, nullable=True)

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
    form_id       = Column(Integer,    ForeignKey("Logs_Forms.id", ondelete="CASCADE"), nullable=False, index=True)
    order_item_id = Column(Integer,    ForeignKey("Stores_Order_Items.id", ondelete="SET NULL"), nullable=True, unique=True)
    item_type     = Column(Text,       nullable=False)
    size          = Column(Text,       nullable=False, default="")  # Tie: "Short"/"Standard"
    cadet_name    = Column(Text,       nullable=False)
    cadet_cin     = Column(BigInteger, nullable=True)  # null for staff orders
    created_at    = Column(DateTime,   nullable=False)

    form = relationship("LogsForm", back_populates="entries")


class BadgeOrderListEntry(Base):
    """A single badge queued for the supplier order. Moves through its own
    queued → ordered → received stages, each stamped with who did it — so the
    QM order list doubles as an audit trail rather than just a checklist."""
    __tablename__ = "Badge_Order_List_Entries"

    id            = Column(Integer,  primary_key=True, autoincrement=True)
    order_item_id = Column(Integer,  ForeignKey("Badge_Order_Items.id", ondelete="SET NULL"), nullable=True, unique=True)
    badge_name    = Column(Text,     nullable=False)
    cadet_name    = Column(Text,     nullable=False)
    created_at    = Column(DateTime, nullable=False)
    added_by      = Column(Text,     nullable=True)
    ordered_at    = Column(DateTime, nullable=True)
    ordered_by    = Column(Text,     nullable=True)
    received_at   = Column(DateTime, nullable=True)
    received_by   = Column(Text,     nullable=True)


# ─── Parade Night Texts ───────────────────────────────────────────────────────

class TextSettings(Base):
    """Squadron-wide settings for the parade-night texts. One row, ever —
    read through texts.settings.get_text_settings, which creates it on first
    use so nothing has to seed it.
    """
    __tablename__ = "Text_Settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Invite link for the squadron's WhatsApp community, shown to anyone who has
    # saved a number. Stored rather than configured in env because WhatsApp
    # invite links get reset, and staff shouldn't need a redeploy to fix one.
    whatsapp_invite_url = Column(Text, nullable=False, default="", server_default="")


class SmsRecipient(Base):
    """A parade-night text recipient with no account behind them.

    Numbers normally live on the Cadet/Staff row they belong to, where their
    owner keeps them up to date. This table is what's left over: parents and
    anyone else on the list who isn't on either roster.
    """
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
    send_results     = Column(JSON, nullable=True)  # [{phone, name, source, status, error?}]


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
    cell_id  = Column(Integer, ForeignKey("Badge_Grid_Cells.id", ondelete="CASCADE"), nullable=False, index=True)
    name     = Column(Text, nullable=False)
    quantity = Column(Integer, nullable=False, default=1, server_default="1")

    cell = relationship("BadgeGridCell", back_populates="items")


# ─── Recruits ─────────────────────────────────────────────────────────────────

class RecruitApplication(Base):
    """A new recruit's joining paperwork (RAFAC 3822A + 3822H), filled in online
    by their parent from a link staff send them. Progress is saved here as they
    go; once submitted, the PDFs are drawn from ``data`` and the signatures on
    demand rather than stored. Kept as a holding area apart from Cadets: a
    recruit has no CIN yet, and nothing here is scraped.
    """
    __tablename__ = "Recruit_Applications"

    id               = Column(Integer,  primary_key=True, autoincrement=True)
    cadet_first_name = Column(Text,     nullable=False)
    cadet_last_name  = Column(Text,     nullable=False)
    parent_name      = Column(Text,     nullable=False)
    parent_email     = Column(Text,     nullable=False)
    # The link's secret. Stored as-is (not hashed) so staff can copy the link
    # again; it only opens this one form, whose answers sit in the same row.
    token            = Column(Text,     nullable=False, unique=True)
    expires_at       = Column(DateTime, nullable=False)
    data             = Column(JSON,     nullable=False, default=dict)  # core.recruit_form.RecruitFormData
    opened_at        = Column(DateTime, nullable=True)
    last_saved_at    = Column(DateTime, nullable=True)
    submitted_at     = Column(DateTime, nullable=True)
    parent_signature = Column(LargeBinary, nullable=True)  # PNG
    cadet_signature  = Column(LargeBinary, nullable=True)  # PNG; only when a cadet of 16+ signs the 3822H
    created_by       = Column(Text,     nullable=True)
    created_at       = Column(DateTime, nullable=False)


# ─── Activity logs (DofE) ─────────────────────────────────────────────────────

class ActivityLogEnrolment(Base):
    """Staff switching a cadet's activity log on. The portal only shows the
    Activity Log page to a cadet with one of these, so it never appears for
    someone who isn't doing their DofE. Turning it off deletes this row and
    keeps the logs, so switching it back on loses nothing."""
    __tablename__ = "Activity_Log_Enrolments"

    cadet_id   = Column(BigInteger, ForeignKey("Cadets.cin", ondelete="CASCADE"), primary_key=True)
    level      = Column(Text, nullable=False, default="Bronze")  # Bronze | Silver | Gold
    enabled_at = Column(DateTime, nullable=False)
    enabled_by = Column(Text, nullable=True)
    dofe_number = Column(Text, nullable=True)
    # Who writes the final report: a staff member (assessor_staff_cin set, so
    # they can filter to their own cadets) or anyone else, name only. Cleared
    # together with the report when the cadet moves up a level.
    assessor_name      = Column(Text, nullable=True)
    assessor_staff_cin = Column(BigInteger, ForeignKey("Staff.cin", ondelete="SET NULL"), nullable=True, index=True)
    # Ticked off by the assessor once every section has run its full length.
    report_done_at = Column(DateTime, nullable=True)
    report_done_by = Column(Text, nullable=True)  # email of the staff member who ticked it

    cadet = relationship("Cadet", back_populates="activity_log_enrolment")


class ActivityLog(Base):
    """One section of a cadet's award (Skills, Volunteering or Physical) at one
    level: what they're doing, for how long, and who signs it off. The paper
    "Activity Log" sheet's header, with its rows in ActivityLogEntry."""
    __tablename__ = "Activity_Logs"
    __table_args__ = (UniqueConstraint("cadet_id", "section", "level", name="uq_activity_log_section"),)

    id              = Column(Integer, primary_key=True, autoincrement=True)
    cadet_id        = Column(BigInteger, ForeignKey("Cadets.cin", ondelete="CASCADE"), nullable=False, index=True)
    section         = Column(Text, nullable=False)  # skills | volunteering | physical
    level           = Column(Text, nullable=False)
    activity        = Column(Text, nullable=False, default="")
    goal            = Column(Text, nullable=False, default="")
    assessor_name   = Column(Text, nullable=False, default="")
    assessor_role   = Column(Text, nullable=False, default="")
    duration_months = Column(Integer, nullable=False)  # 3, 6, 12 or 18
    start_date      = Column(Date, nullable=False)
    # Set when the last week needed is logged and staff have been emailed the
    # PDF; cleared if edits take it back under, so finishing again re-sends it.
    completed_at    = Column(DateTime, nullable=True)
    created_at      = Column(DateTime, nullable=False)
    updated_at      = Column(DateTime, nullable=False)

    cadet   = relationship("Cadet", back_populates="activity_logs")
    entries = relationship("ActivityLogEntry", back_populates="log", cascade="all, delete-orphan",
                           order_by="ActivityLogEntry.date")


class ActivityLogEntry(Base):
    """One thing the cadet did on one day, and for how long."""
    __tablename__ = "Activity_Log_Entries"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    log_id      = Column(Integer, ForeignKey("Activity_Logs.id", ondelete="CASCADE"), nullable=False, index=True)
    date        = Column(Date, nullable=False)
    description = Column(Text, nullable=False)
    minutes     = Column(Integer, nullable=False)
    created_at  = Column(DateTime, nullable=False)
    created_by  = Column(Text, nullable=True)  # email: the cadet, or the staff member who added it
    # The assessor's initials in the PDF's Initials column. They can be someone
    # else's (an instructor who saw it), so who clicked is kept separately.
    signed_off_initials = Column(Text, nullable=True)
    signed_off_by       = Column(Text, nullable=True)  # email of the staff member who recorded it
    signed_off_at       = Column(DateTime, nullable=True)

    log = relationship("ActivityLog", back_populates="entries")


# ─── Usage ────────────────────────────────────────────────────────────────────

class UsageDaily(Base):
    """Successful API calls, counted per day, endpoint and caller. Aggregated
    rather than one row per request so frontends that poll can't bloat it."""
    __tablename__ = "Usage_Daily"
    __table_args__ = (UniqueConstraint("day", "method", "route", "email", name="uq_usage_daily"),)

    id      = Column(Integer, primary_key=True, autoincrement=True)
    day     = Column(Date, nullable=False)
    method  = Column(Text, nullable=False)
    route   = Column(Text, nullable=False)                 # route template, e.g. /cadets/{cin}
    email   = Column(Text, nullable=False, default="")     # "" for unauthenticated calls
    calls   = Column(Integer, nullable=False, default=0)
    last_at = Column(DateTime, nullable=False)


# ─── Audit Trail ──────────────────────────────────────────────────────────────

class AuditEvent(Base):
    """Who did what, for actions the domain tables don't already stamp.

    The squadron holds records on minors, including medical details, so reads
    of that data have to be answerable after the fact — an actor column on the
    record itself can't show who merely *looked*. This covers those reads plus
    deletions and anything that leaves the system (SMS sends, DB restores).

    Deliberately not a full request log: it records the handful of actions
    worth answering for, so it stays small enough to keep for a long retention
    and readable when someone actually has to audit it.
    """
    __tablename__ = "Audit_Events"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    occurred_at = Column(DateTime, nullable=False, index=True)  # indexed for the retention sweep
    actor_email = Column(Text, nullable=False)
    actor_role  = Column(Text, nullable=True)   # staff/snco/nco at the time of the action
    action      = Column(Text, nullable=False)  # e.g. "read", "delete", "send", "restore"
    resource    = Column(Text, nullable=False)  # e.g. "cadet_medical", "stores_order"
    resource_id = Column(Text, nullable=True)   # text so ints and composite keys both fit
    detail      = Column(JSON, nullable=True)   # small extras, e.g. {"count": 12}


# ─── Push notifications ──────────────────────────────────────────────────────

class PushSubscription(Base):
    """One browser or installed app that has agreed to take push notifications.

    The endpoint and keys come from the browser's PushManager and are only
    useful to whoever holds our VAPID private key. A person can have several
    (phone and laptop); each picks its own topics, so the laptop can stay quiet
    while the phone buzzes. Deleted when the push service says it has gone.
    """
    __tablename__ = "Push_Subscriptions"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    email        = Column(Text, nullable=False, index=True)    # lower-cased
    endpoint     = Column(Text, nullable=False, unique=True)
    p256dh       = Column(Text, nullable=False)
    auth         = Column(Text, nullable=False)
    topics       = Column(JSON, nullable=False, default=list)  # keys of core.push.TOPICS
    user_agent   = Column(Text, nullable=True)
    created_at   = Column(DateTime, nullable=False)
    last_sent_at = Column(DateTime, nullable=True)


# ─── Assistant chats ─────────────────────────────────────────────────────────

class AssistantChat(Base):
    """One conversation with the squadron assistant (routers/assistant.py).

    Kept per person so a chat can be picked up again later, and only ever
    shown to the account that started it: questions and answers can name
    cadets with their dates of birth and emails.
    """
    __tablename__ = "Assistant_Chats"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    email      = Column(Text, nullable=False, index=True)  # lower-cased owner
    title      = Column(Text, nullable=False)              # the first question cut short, or renamed
    pinned     = Column(Boolean, nullable=False, default=False, server_default="0")  # top of the list
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)          # newest first in the list

    messages = relationship("AssistantMessage", back_populates="chat", cascade="all, delete-orphan",
                            order_by="AssistantMessage.id")


class AssistantMessage(Base):
    """One question or answer in an assistant chat."""
    __tablename__ = "Assistant_Messages"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    chat_id    = Column(Integer, ForeignKey("Assistant_Chats.id", ondelete="CASCADE"), nullable=False, index=True)
    role       = Column(Text, nullable=False)  # "user" | "assistant"
    content    = Column(Text, nullable=False)  # the question, or the answer's Markdown
    # An answer's extras: {charts, tables, follow_ups, calls, model}. None on a question.
    extra      = Column(JSON, nullable=True)
    # The asker's thumbs on an answer: 1 up, -1 down, None not rated. The
    # owner's usage view lists the thumbs-down, which is how wrong answers
    # get found.
    feedback   = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False)

    chat = relationship("AssistantChat", back_populates="messages")


# ─── Programme ───────────────────────────────────────────────────────────────

class ProgrammeNight(Base):
    """One parade night from the monthly programme doc (core/programme.py).
    A month is replaced as a whole on each import, so editing the doc and
    re-importing is how a change gets in."""
    __tablename__ = "Programme_Nights"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    date        = Column(Date, nullable=False, unique=True)
    uniform     = Column(Text, nullable=True)
    duty_nco    = Column(Text, nullable=True)
    imported_at = Column(DateTime, nullable=False)

    sessions = relationship("ProgrammeSession", back_populates="night", cascade="all, delete-orphan",
                            order_by="ProgrammeSession.id")


class ProgrammeSession(Base):
    """What one flight did in one period of a night: one row per flight per
    activity, so a whole-squadron session is a row for each of C, A and B and
    "what was B Flight doing" is one lookup."""
    __tablename__ = "Programme_Sessions"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    night_id   = Column(Integer, ForeignKey("Programme_Nights.id", ondelete="CASCADE"), nullable=False, index=True)
    period     = Column(Integer, nullable=False)   # 1 or 2
    flight     = Column(Text,    nullable=False)   # "C" (probationary), "A" or "B"
    activity   = Column(Text,    nullable=False)   # tidy name, e.g. "Map Reading 2"
    category   = Column(Text,    nullable=False)   # a core.programme.CATEGORIES key
    instructor = Column(Text,    nullable=True)
    # The flight was divided between this and something else at the same time,
    # so being there that night doesn't say a cadet did this one.
    split      = Column(Boolean, nullable=False, default=False, server_default="0")
    raw        = Column(Text,    nullable=False)   # the cell as written, which a re-import matches on

    night = relationship("ProgrammeNight", back_populates="sessions")
