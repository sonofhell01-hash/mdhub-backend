from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class OperationalTemplateCreate(BaseModel):
    document_type: Literal["laudo", "rat"]
    category: str = Field(default="", max_length=50)
    label: str = Field(min_length=3, max_length=140)
    payload: dict[str, Any]


class OperationalTemplateResponse(BaseModel):
    id: int
    document_type: str
    category: str
    label: str
    payload: dict[str, Any]
    source: str
    active: bool
    created_by_user_id: int
    created_at: datetime

    model_config = {"from_attributes": True}
