from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ToolCall(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=80)
    arguments: dict = Field(default_factory=dict)


class Report(BaseModel):
    model_config = ConfigDict(extra='forbid')
    summary: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[str] = Field(max_length=100)


class Decision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['tools', 'final', 'handoff']
    calls: list[ToolCall] = Field(default_factory=list, max_length=4)
    report: Report | None = None
    reason: str = Field(default='', max_length=500)
    usage: int = Field(default=0, ge=0)


class Query(BaseModel):
    model_config = ConfigDict(extra='forbid')
    asset: str | None = Field(default=None, max_length=200)
    account: str | None = Field(default=None, max_length=200)
    source_ip: str | None = Field(default=None, max_length=64)
    path: str | None = Field(default=None, max_length=500)
    start: str | None = Field(default=None, max_length=40)
    end: str | None = Field(default=None, max_length=40)
    cursor: str | None = Field(default=None, max_length=500)
    limit: int = Field(default=50, ge=1, le=100)
