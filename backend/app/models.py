from datetime import date, datetime
from typing import Optional

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class Vessel(TimestampMixin, Base):
    __tablename__ = "vessels"

    id: Mapped[int] = mapped_column(primary_key=True)
    imo: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    chinese_name: Mapped[Optional[str]] = mapped_column(String(128))
    english_name: Mapped[Optional[str]] = mapped_column(String(128))
    nationality: Mapped[Optional[str]] = mapped_column(String(64))
    call_sign: Mapped[Optional[str]] = mapped_column(String(64))
    shipping_company: Mapped[Optional[str]] = mapped_column(String(128))
    net_tonnage: Mapped[Optional[int]] = mapped_column(Integer)
    gross_tonnage: Mapped[Optional[int]] = mapped_column(Integer)
    mmsi: Mapped[Optional[str]] = mapped_column(String(32))
    extra_json: Mapped[str] = mapped_column(Text, default="{}")


class Voyage(TimestampMixin, Base):
    __tablename__ = "voyages"

    id: Mapped[int] = mapped_column(primary_key=True)
    vessel_id: Mapped[int] = mapped_column(ForeignKey("vessels.id"), index=True)
    inbound_voyage_no: Mapped[Optional[str]] = mapped_column(String(64))
    outbound_voyage_no: Mapped[Optional[str]] = mapped_column(String(64))
    arrival_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    departure_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    berth: Mapped[Optional[str]] = mapped_column(String(128))
    previous_port: Mapped[Optional[str]] = mapped_column(String(128))
    previous_port_country: Mapped[Optional[str]] = mapped_column(String(64))
    previous_port_departure_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    next_port: Mapped[Optional[str]] = mapped_column(String(128))
    next_port_country: Mapped[Optional[str]] = mapped_column(String(64))
    route: Mapped[Optional[str]] = mapped_column(String(256))
    entry_type: Mapped[Optional[str]] = mapped_column(String(16))
    crew_change: Mapped[bool] = mapped_column(default=False)
    extra_json: Mapped[str] = mapped_column(Text, default="{}")


class CrewManifest(TimestampMixin, Base):
    __tablename__ = "crew_manifests"

    id: Mapped[int] = mapped_column(primary_key=True)
    voyage_id: Mapped[int] = mapped_column(ForeignKey("voyages.id"), index=True)
    source_filename: Mapped[Optional[str]] = mapped_column(String(256))
    source_type: Mapped[Optional[str]] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer, default=1)


class CrewMember(Base):
    __tablename__ = "crew_members"
    __table_args__ = (UniqueConstraint("manifest_id", "document_no", name="uq_manifest_document"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    manifest_id: Mapped[int] = mapped_column(ForeignKey("crew_manifests.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    gender: Mapped[Optional[str]] = mapped_column(String(16))
    nationality: Mapped[Optional[str]] = mapped_column(String(64))
    birth_date: Mapped[Optional[date]] = mapped_column(Date)
    document_no: Mapped[Optional[str]] = mapped_column(String(64))
    rank: Mapped[Optional[str]] = mapped_column(String(64))
    extra_json: Mapped[str] = mapped_column(Text, default="{}")


class CrewChangePerson(Base):
    __tablename__ = "crew_change_people"

    id: Mapped[int] = mapped_column(primary_key=True)
    voyage_id: Mapped[int] = mapped_column(ForeignKey("voyages.id"), index=True)
    direction: Mapped[str] = mapped_column(String(8))
    name: Mapped[str] = mapped_column(String(128))
    nationality: Mapped[Optional[str]] = mapped_column(String(64))
    gender: Mapped[Optional[str]] = mapped_column(String(16))
    birth_date: Mapped[Optional[date]] = mapped_column(Date)
    document_no: Mapped[Optional[str]] = mapped_column(String(64))
    rank: Mapped[Optional[str]] = mapped_column(String(64))
    reason: Mapped[Optional[str]] = mapped_column(String(128))
    temporary_entry_permit: Mapped[Optional[bool]] = mapped_column()
    flight_no: Mapped[Optional[str]] = mapped_column(String(64))
    flight_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    route: Mapped[Optional[str]] = mapped_column(String(256))


class TonnageApplication(TimestampMixin, Base):
    __tablename__ = "tonnage_applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    voyage_id: Mapped[int] = mapped_column(ForeignKey("voyages.id"), index=True)
    amount: Mapped[Optional[str]] = mapped_column(String(32))
    pre_entry_no: Mapped[Optional[str]] = mapped_column(String(64))
    duration_days: Mapped[Optional[int]] = mapped_column(Integer)
    purchase_date: Mapped[Optional[date]] = mapped_column(Date)
    charter_relation: Mapped[Optional[str]] = mapped_column(String(64))


class Forecast(Base):
    __tablename__ = "forecasts"

    id: Mapped[int] = mapped_column(primary_key=True)
    voyage_id: Mapped[int] = mapped_column(ForeignKey("voyages.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[str] = mapped_column(Text)
    missing_fields_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
