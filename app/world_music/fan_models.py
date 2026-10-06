"""Persistent fan CRM tables, independent of API routes."""
import secrets
from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, new_id


class FanContact(Base):
    __tablename__ = "fan_contacts"
    __table_args__ = (UniqueConstraint("artist_id", "email"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    artist_id: Mapped[str] = mapped_column(ForeignKey("artists.id"))
    email: Mapped[str] = mapped_column(String(254))
    name: Mapped[str] = mapped_column(String(120), default="")
    source: Mapped[str] = mapped_column(String(240))
    consent_at: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="subscribed")
    token: Mapped[str] = mapped_column(String(64), unique=True, default=lambda: secrets.token_urlsafe(32))


class FanCampaign(Base):
    __tablename__ = "fan_campaigns"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    artist_id: Mapped[str] = mapped_column(ForeignKey("artists.id"))
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(240), default="")
    status: Mapped[str] = mapped_column(String(20), default="draft")


class FanRecipient(Base):
    __tablename__ = "fan_recipients"
    __table_args__ = (UniqueConstraint("campaign_id", "contact_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("fan_campaigns.id"))
    contact_id: Mapped[str] = mapped_column(ForeignKey("fan_contacts.id"))
    status: Mapped[str] = mapped_column(String(20), default="pending")


