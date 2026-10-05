from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ProviderState = Literal[
    "CONNECTED",
    "DEGRADED",
    "PROVIDER_NOT_CONNECTED",
    "LICENSE_SCOPE_REQUIRED",
    "CONFIGURED_UNVERIFIED",
    "CONNECTED_UNVERIFIED_RIGHTS",
]
ConfidenceState = Literal["HIGH", "MEDIUM", "LOW", "INSUFFICIENT"]


class ProviderStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_name: str
    state: ProviderState
    country_coverage: list[str] = Field(default_factory=list)
    metrics_available: list[str] = Field(default_factory=list)
    last_refresh: datetime | None = None
    data_confidence: ConfidenceState = "INSUFFICIENT"
    rate_limit_status: str = "NOT_CONFIGURED"
    setup_action: str | None = None
    access_note: str | None = None
    freshness_note: str | None = None


class MusicDataProvider(ABC):
    """Adapter contract. Implementations must report real coverage and limits."""

    @abstractmethod
    def status(self) -> ProviderStatus:
        raise NotImplementedError


class UnconfiguredProvider(MusicDataProvider):
    def __init__(self, provider_name: str):
        self.provider_name = provider_name

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            provider_name=self.provider_name,
            state="PROVIDER_NOT_CONNECTED",
            data_confidence="INSUFFICIENT",
            rate_limit_status="NOT_CONFIGURED",
        )
