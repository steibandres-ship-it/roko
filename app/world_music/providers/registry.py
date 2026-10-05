from __future__ import annotations

from .base import MusicDataProvider, ProviderStatus
from .chartmetric import ChartmetricProvider
from .lastfm import LastFmProvider
from .soundcharts import SoundchartsProvider


class ProviderRegistry:
    def __init__(self, providers: list[MusicDataProvider] | None = None):
        if providers is not None:
            self.providers = providers
            return
        lastfm = LastFmProvider.from_environment()
        chartmetric = ChartmetricProvider.from_environment()
        soundcharts = SoundchartsProvider.from_environment()
        self.providers = [
            soundcharts if soundcharts is not None else SoundchartsProvider(),
            chartmetric if chartmetric is not None else ChartmetricProvider(""),
            lastfm if lastfm is not None else LastFmProvider(""),
        ]

    def statuses(self) -> list[ProviderStatus]:
        return [provider.status() for provider in self.providers]
