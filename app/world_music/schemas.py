from __future__ import annotations

import re
import math
from datetime import date, datetime
from typing import Literal

import pycountry
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


RightsBasis = Literal["owned", "consented", "licensed"]
MarketScope = Literal["world", "region", "country", "subdivision", "city", "scene"]
GenreLevel = Literal["family", "genre", "subgenre", "microgenre", "scene"]


class ExternalIdentifierInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(min_length=2, max_length=80)
    identifier: str = Field(min_length=1, max_length=160)

    @field_validator("provider")
    @classmethod
    def normalize_provider(cls, value: str) -> str:
        normalized = re.sub(r"[^a-z0-9-]+", "-", value.strip().casefold()).strip("-")
        if len(normalized) < 2:
            raise ValueError("provider must contain at least two letters or numbers")
        return normalized


class ProvenanceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_name: str = Field(min_length=2, max_length=120)
    rights_basis: RightsBasis
    source_url: str | None = Field(default=None, max_length=500)
    captured_at: datetime
    data_confidence: float = Field(ge=0, le=1)

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("https://"):
            raise ValueError("source_url must use HTTPS")
        return value


class MarketInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[A-Z0-9]+(?:[_-][A-Z0-9]+)*$", max_length=80)
    name: str = Field(min_length=1, max_length=120)
    scope: MarketScope
    iso_code: str | None = Field(default=None, min_length=2, max_length=2)
    parent_key: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def validate_country(self) -> "MarketInput":
        if self.scope == "country":
            code = (self.iso_code or "").upper()
            if len(code) != 2 or pycountry.countries.get(alpha_2=code) is None:
                raise ValueError("country markets require a valid ISO 3166-1 alpha-2 iso_code")
            self.iso_code = code
        elif self.iso_code is not None:
            raise ValueError("iso_code is only valid for country markets")
        return self


class GenreInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)
    name: str = Field(min_length=1, max_length=120)
    level: GenreLevel
    parent_slug: str | None = Field(default=None, max_length=100)


class ArtistInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)
    name: str = Field(min_length=1, max_length=200)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    external_identifiers: list[ExternalIdentifierInput] = Field(min_length=1, max_length=10)
    genre_slugs: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("country_code")
    @classmethod
    def validate_country_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        code = value.upper()
        if pycountry.countries.get(alpha_2=code) is None:
            raise ValueError("country_code must be a valid ISO 3166-1 alpha-2 code")
        return code


class ReleaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)
    title: str = Field(min_length=1, max_length=240)
    release_date: date | None = None
    external_identifiers: list[ExternalIdentifierInput] = Field(min_length=1, max_length=10)


class GenreWeightInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    genre_slug: str = Field(min_length=1, max_length=100)
    weight: float = Field(gt=0, le=1)


class TrackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)
    title: str = Field(min_length=1, max_length=240)
    isrc: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]{12}$")
    release_ref: str | None = Field(default=None, max_length=100)
    artist_refs: list[str] = Field(min_length=1, max_length=30)
    external_identifiers: list[ExternalIdentifierInput] = Field(default_factory=list, max_length=10)
    languages: list[str] = Field(default_factory=list, max_length=20)
    genre_weights: list[GenreWeightInput] = Field(default_factory=list, max_length=30)
    market_keys: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("isrc")
    @classmethod
    def normalize_isrc(cls, value: str | None) -> str | None:
        return value.upper() if value else value

    @model_validator(mode="after")
    def require_identity(self) -> "TrackInput":
        if not self.isrc and not self.external_identifiers:
            raise ValueError("tracks require an ISRC or an external identifier")
        if len({item.genre_slug for item in self.genre_weights}) != len(self.genre_weights):
            raise ValueError("genre_weights cannot repeat a genre_slug")
        if self.genre_weights and not math.isclose(sum(item.weight for item in self.genre_weights), 1.0, abs_tol=0.01):
            raise ValueError("weighted genre memberships must sum to 1.0 within 0.01")
        return self


class CatalogImportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provenance: ProvenanceInput
    markets: list[MarketInput] = Field(default_factory=list, max_length=500)
    genres: list[GenreInput] = Field(default_factory=list, max_length=2000)
    artists: list[ArtistInput] = Field(default_factory=list, max_length=10000)
    releases: list[ReleaseInput] = Field(default_factory=list, max_length=10000)
    tracks: list[TrackInput] = Field(default_factory=list, max_length=50000)

    @model_validator(mode="after")
    def validate_references(self) -> "CatalogImportInput":
        if not any((self.markets, self.genres, self.artists, self.releases, self.tracks)):
            raise ValueError("an import batch must contain at least one catalog or market record")

        def unique(values: list[str], label: str) -> set[str]:
            result = set(values)
            if len(result) != len(values):
                raise ValueError(f"{label} keys must be unique within an import batch")
            return result

        market_keys = unique([item.key for item in self.markets], "market")
        genre_slugs = unique([item.slug for item in self.genres], "genre")
        artist_keys = unique([item.key for item in self.artists], "artist")
        release_keys = unique([item.key for item in self.releases], "release")
        unique([item.key for item in self.tracks], "track")
        for market in self.markets:
            if market.parent_key and market.parent_key not in market_keys:
                raise ValueError(f"unknown parent market key: {market.parent_key}")
        for genre in self.genres:
            if genre.parent_slug and genre.parent_slug not in genre_slugs:
                raise ValueError(f"unknown parent genre slug: {genre.parent_slug}")
        for artist in self.artists:
            if set(artist.genre_slugs) - genre_slugs:
                raise ValueError("artist references a genre slug missing from this import batch")
        for track in self.tracks:
            if set(track.artist_refs) - artist_keys:
                raise ValueError("track references an artist key missing from this import batch")
            if track.release_ref and track.release_ref not in release_keys:
                raise ValueError("track references a release key missing from this import batch")
            if {item.genre_slug for item in track.genre_weights} - genre_slugs:
                raise ValueError("track references a genre slug missing from this import batch")
            if set(track.market_keys) - market_keys:
                raise ValueError("track references a market key missing from this import batch")
        return self
