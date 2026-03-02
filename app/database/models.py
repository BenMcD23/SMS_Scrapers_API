from sqlalchemy import (
    Column,
    Integer,
    Float,
    Boolean,
    Text,
    DateTime,
    ForeignKey,
    LargeBinary,
)
from sqlalchemy.orm import relationship

from database.database import Base


# ─── Cadet / Event tables (unchanged) ────────────────────────────────────────

class Cadet(Base):
    __tablename__ = "Cadets"

    cin = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, nullable=False)
    event_banned = Column(Boolean, nullable=False)

    cadet_events = relationship("CadetEvent", back_populates="cadet")


class Event317(Base):
    __tablename__ = "317_Events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(Text, nullable=False)
    reference = Column(Text, nullable=False)
    adult_ic = Column(Text, nullable=False)
    contact_number = Column(Integer, nullable=False)
    date_from = Column(DateTime, nullable=False)
    date_to = Column(DateTime, nullable=False)
    location_id = Column(Integer, ForeignKey("Location.id"), nullable=False)
    cost = Column(Float, nullable=False)
    dress = Column(Text, nullable=False)
    description = Column(Text, nullable=False)

    location = relationship("Location", back_populates="events")


class Location(Base):
    __tablename__ = "Location"

    id = Column(Integer, primary_key=True, autoincrement=True)
    first_line = Column(Text, nullable=False)
    postcode = Column(Text, nullable=False)

    events = relationship("Event317", back_populates="location")


class CadetEvent(Base):
    __tablename__ = "Cadet_Events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(Integer, ForeignKey("All_Events.id"), nullable=False)
    cadet_id = Column(Integer, ForeignKey("Cadets.cin"), nullable=False)

    cadet = relationship("Cadet", back_populates="cadet_events")
    event = relationship("AllEvent", back_populates="cadet_events")
    ban_notifications = relationship("BanNotification", back_populates="cadet_event")


class BanNotification(Base):
    __tablename__ = "Ban_Notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cadet_event_id = Column(Integer, ForeignKey("Cadet_Events.id"), nullable=False)
    email_sent = Column(Boolean, nullable=False)

    cadet_event = relationship("CadetEvent", back_populates="ban_notifications")


class AllEvent(Base):
    __tablename__ = "All_Events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(Text, nullable=False)

    cadet_events = relationship("CadetEvent", back_populates="event")


# ─── User tables ──────────────────────────────────────────────────────────────

class User(Base):
    """
    Core identity record — created automatically on first login/action.
    Holds only auth identity; credentials and signature are in child tables.
    """
    __tablename__ = "Users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    google_id = Column(Text, unique=True, nullable=False, index=True)
    email = Column(Text, nullable=False)

    bader_credentials = relationship(
        "BaderCredentials",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
    signature = relationship(
        "UserSignature",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )


class BaderCredentials(Base):
    """
    Bader login credentials — one row per user, created/updated via /save-credentials.
    """
    __tablename__ = "Bader_Credentials"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer, ForeignKey("Users.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    role_username = Column(Text, nullable=True)
    role_password = Column(Text, nullable=True)       # stored encrypted
    personal_username = Column(Text, nullable=True)
    personal_password = Column(Text, nullable=True)   # stored encrypted

    user = relationship("User", back_populates="bader_credentials")


class UserSignature(Base):
    """
    Assessor signature image — one row per user, created/replaced via /save-signature.
    """
    __tablename__ = "User_Signatures"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer, ForeignKey("Users.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    image_data = Column(LargeBinary, nullable=False)
    mime_type = Column(Text, nullable=False, default="image/png")

    user = relationship("User", back_populates="signature")