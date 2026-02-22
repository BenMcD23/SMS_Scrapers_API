from sqlalchemy import (
    Column,
    Integer,
    Float,
    Boolean,
    Text,
    DateTime,
    ForeignKey
)
from sqlalchemy.orm import relationship, declarative_base

from database.database import Base



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

# In your models file
class User(Base):
    __tablename__ = "Users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    google_id = Column(Text, unique=True, nullable=False) # The ID from NextAuth
    email = Column(Text, nullable=False)
    
    # Bader Credentials (encrypted in a real app, but for now plain text)
    role_username = Column(Text)
    role_password = Column(Text)
    personal_username = Column(Text)
    personal_password = Column(Text)