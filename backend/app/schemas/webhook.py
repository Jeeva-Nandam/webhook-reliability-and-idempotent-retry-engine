"""
Pydantic schemas: the wire format. Deliberately kept separate from the
SQLAlchemy models in app/db/models -- the DB shape and the API shape are
allowed to diverge (e.g. we never expose internal id, only event_id).
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class WebhookIngestRequest(BaseModel):
    event_id: str = Field(..., min_length=1, max_length=255)
    event_type: str = Field(..., min_length=1, max_length=255)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_id", "event_type")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v


class WebhookAttemptResponse(BaseModel):
    attempt_number: int
    status: str
    started_at: datetime
    completed_at: datetime | None
    response_code: int | None
    error_message: str | None

    class Config:
        from_attributes = True


class WebhookEventResponse(BaseModel):
    event_id: str
    event_type: str
    payload: dict[str, Any]
    status: str
    retry_count: int
    max_retries: int
    last_attempt_at: datetime | None
    next_retry_at: datetime | None
    processed_at: datetime | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class WebhookEventDetailResponse(WebhookEventResponse):
    attempts: list[WebhookAttemptResponse] = Field(default_factory=list)


class WebhookIngestResponse(BaseModel):
    event_id: str
    status: str
    message: str


class PaginatedWebhookEvents(BaseModel):
    items: list[WebhookEventResponse]
    page: int
    page_size: int
    total: int


class MetricsSummaryResponse(BaseModel):
    total_events: int
    success_count: int
    failed_count: int
    dead_count: int
    processing_count: int
    pending_count: int
    success_rate: float


class DownstreamSimulationRequest(BaseModel):
    mode: str = Field(
        default="success",
        description="one of: success, error_500, timeout, random",
    )
