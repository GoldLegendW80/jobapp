"""Owned external candidate profile API contracts. Browser refs and capabilities stay private."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field


class StartIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    url: str = Field(min_length=1, max_length=2048)
    autosave_confirmed: bool = False


class FieldIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: Annotated[str, Field(max_length=10000)] | bool | None = None
    replace: bool | None = None


class ApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    values_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class FieldOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    field_key: str
    label: str
    kind: str
    value: Any
    current_value: Any
    source: str
    source_path: str
    required: bool
    refusal: str | None
    options: list[str]
    replace: bool
    approved: bool


class ShotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    kind: str
    section: str
    created_at: datetime


class SectionOut(BaseModel):
    section: str
    label: str
    position: int
    state: str
    error_code: str | None
    values_hash: str
    fields: list[FieldOut]
    screenshots: list[ShotOut]
    events: list[dict[str, Any]]


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    url: str
    host: str
    site_kind: str
    state: str
    autosave: bool
    autosave_confirmed: bool
    needs: str | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    cost: dict[str, Any]


class RunDetail(RunOut):
    sections: list[SectionOut]


class RunList(BaseModel):
    items: list[RunOut]
