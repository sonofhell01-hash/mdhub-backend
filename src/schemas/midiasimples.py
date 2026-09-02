from typing import Any

from pydantic import BaseModel, Field


class MidiaSimplesCredentials(BaseModel):
    email: str
    password: str


class DataTablePage(BaseModel):
    total: int = 0
    filtered: int = 0
    count: int = 0
    data: list[dict[str, Any]] = Field(default_factory=list)


class MidiaSimplesSearchRequest(MidiaSimplesCredentials):
    search: str = ""
    start: int = 0
    length: int = 10
