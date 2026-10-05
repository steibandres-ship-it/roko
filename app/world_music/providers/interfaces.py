from __future__ import annotations

from abc import abstractmethod
from typing import Any

from .base import MusicDataProvider


class TrendDataProvider(MusicDataProvider):
    @abstractmethod
    def fetch_trends(self, **filters: Any) -> list[dict[str, Any]]:
        raise NotImplementedError


class ArtistMetricsProvider(MusicDataProvider):
    @abstractmethod
    def fetch_artist_metrics(self, artist_ids: list[str]) -> list[dict[str, Any]]:
        raise NotImplementedError


class TrackMetricsProvider(MusicDataProvider):
    @abstractmethod
    def fetch_track_metrics(self, track_ids: list[str]) -> list[dict[str, Any]]:
        raise NotImplementedError


class SocialMetricsProvider(MusicDataProvider):
    @abstractmethod
    def fetch_social_metrics(self, artist_ids: list[str], track_ids: list[str]) -> list[dict[str, Any]]:
        raise NotImplementedError


class ChartProvider(TrendDataProvider):
    @abstractmethod
    def fetch_charts(self, **filters: Any) -> list[dict[str, Any]]:
        raise NotImplementedError


class DiscoveryProvider(MusicDataProvider):
    @abstractmethod
    def fetch_discovery_signals(self, **filters: Any) -> list[dict[str, Any]]:
        raise NotImplementedError


class MarketProvider(MusicDataProvider):
    @abstractmethod
    def fetch_market_data(self, market_codes: list[str]) -> list[dict[str, Any]]:
        raise NotImplementedError
