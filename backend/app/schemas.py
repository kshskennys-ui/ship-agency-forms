from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class VesselCreate(BaseModel):
    imo: Optional[str] = None
    chinese_name: Optional[str] = None
    english_name: Optional[str] = None
    nationality: Optional[str] = None
    call_sign: Optional[str] = None
    shipping_company: Optional[str] = None
    net_tonnage: Optional[int] = None
    gross_tonnage: Optional[int] = None
    mmsi: Optional[str] = None
    extra: dict[str, Any] = Field(default_factory=dict)


class TextExtractRequest(BaseModel):
    text: str = Field(min_length=1)


class VesselRead(VesselCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int


class VoyageCreate(BaseModel):
    vessel_id: int
    inbound_voyage_no: Optional[str] = None
    outbound_voyage_no: Optional[str] = None
    arrival_time: Optional[datetime] = None
    departure_time: Optional[datetime] = None
    berth: Optional[str] = None
    previous_port: Optional[str] = None
    previous_port_country: Optional[str] = None
    previous_port_departure_time: Optional[datetime] = None
    next_port: Optional[str] = None
    next_port_country: Optional[str] = None
    route: Optional[str] = None
    entry_type: Optional[str] = None
    crew_change: bool = False
    customs_inspection: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)


class VoyageUpdate(VoyageCreate):
    pass


class CrewChangePersonCreate(BaseModel):
    direction: str = Field(pattern="^(up|down)$")
    name: str
    nationality: Optional[str] = None
    gender: Optional[str] = None
    birth_date: Optional[date] = None
    document_no: Optional[str] = None
    rank: Optional[str] = None
    reason: Optional[str] = None
    temporary_entry_permit: Optional[bool] = None
    flight_no: Optional[str] = None
    flight_time: Optional[datetime] = None
    route: Optional[str] = None


class CrewChangeCreate(BaseModel):
    people: list[CrewChangePersonCreate]


class TemporaryEntryApplicantCreate(BaseModel):
    crew_member_id: int


class ExitStampApplicantCreate(BaseModel):
    crew_member_id: int


class CrewChangePersonUpdate(CrewChangePersonCreate):
    pass


class TonnageCreate(BaseModel):
    amount: Optional[str] = None
    pre_entry_no: Optional[str] = None
    duration_days: Optional[int] = None
    purchase_date: Optional[date] = None
    charter_relation: Optional[str] = None


class VoyageSummary(BaseModel):
    voyage: dict[str, Any]
    vessel: dict[str, Any]
    crew_count: int
    captain: Optional[str]
    nationality_stats: dict[str, int]
    gender_stats: dict[str, int]
    crew: list[dict[str, Any]]
    crew_change: list[dict[str, Any]]
    tonnage: Optional[dict[str, Any]]
    latest_forecast: Optional[dict[str, Any]]
