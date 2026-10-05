from __future__ import annotations

import re
import math
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Priority = Literal["anchor", "growth", "discovery"]
NeuromentalIntent = Literal[
    "activation",
    "uplift",
    "focus",
    "social-connection",
    "reflection",
    "wind-down",
    "discovery",
    "self-expression",
    "curiosity",
]
SequenceRole = Literal["warm-up", "build", "flow", "peak", "release", "cool-down", "discovery"]
PublicPlaylistType = Literal["ELITE", "NOW", "RISING", "BREAKOUT", "DISCOVERY", "NEXT", "NEW_MUSIC"]
ArtistStage = Literal["seed", "emerging", "breakout", "scaling", "elite", "core"]


def extract_track_id(value: str) -> str:
    value = value.strip()
    uri_match = re.fullmatch(r"spotify:track:([A-Za-z0-9]{1,64})", value)
    if uri_match:
        return uri_match.group(1)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"open.spotify.com", "www.open.spotify.com"}:
        raise ValueError("Expected a Spotify track URI or open.spotify.com track URL")
    parts = [part for part in parsed.path.split("/") if part]
    try:
        index = parts.index("track")
        track_id = parts[index + 1]
    except (ValueError, IndexError) as exc:
        raise ValueError("Spotify URL does not contain a track ID") from exc
    if not re.fullmatch(r"[A-Za-z0-9]{1,64}", track_id):
        raise ValueError("Spotify track ID has an invalid format")
    return track_id


def track_uri(track_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9]{1,64}", track_id):
        raise ValueError("Spotify track ID has an invalid format")
    return f"spotify:track:{track_id}"


class TrackNeuromentalMeta(BaseModel):
    """Human-curated listening-context signals; never inferred from Spotify data."""

    model_config = ConfigDict(extra="forbid")

    energy: int | None = Field(default=None, ge=1, le=5)
    valence: int | None = Field(default=None, ge=1, le=5)
    focus: int | None = Field(default=None, ge=1, le=5)
    social_energy: int | None = Field(default=None, ge=1, le=5)
    intent_tags: list[NeuromentalIntent] = Field(default_factory=list)
    sequence_role: SequenceRole | None = None
    rationale: str | None = Field(default=None, max_length=280)


class TrackSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artist: str | None = None
    title: str | None = None
    spotify_uri: str | None = None
    spotify_url: str | None = None
    isrc: str | None = None
    priority: Priority = "growth"
    locked: bool = False
    spotify_catalog_verified: bool = False
    neuromental: TrackNeuromentalMeta | None = None

    @model_validator(mode="after")
    def has_identifier(self) -> "TrackSpec":
        has_direct_id = bool(self.spotify_uri or self.spotify_url)
        has_search_fields = bool(self.artist and self.title)
        if not has_direct_id and not has_search_fields:
            raise ValueError("provide spotify_uri, spotify_url, or both artist and title")
        if self.spotify_uri:
            if not self.spotify_uri.startswith("spotify:track:"):
                raise ValueError("spotify_uri must be a track URI")
            extract_track_id(self.spotify_uri)
        if self.spotify_url:
            extract_track_id(self.spotify_url)
        if self.spotify_uri and self.spotify_url and extract_track_id(self.spotify_uri) != extract_track_id(self.spotify_url):
            raise ValueError("spotify_uri and spotify_url refer to different tracks")
        if self.isrc and not re.fullmatch(r"[A-Za-z0-9]{12}", self.isrc):
            raise ValueError("isrc must contain 12 letters or numbers")
        return self


class RotationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    keep_anchor_tracks: int = Field(default=20, ge=0)
    growth_slots: int = Field(default=40, ge=0)
    discovery_slots: int = Field(default=20, ge=0)


class NeuromentalProfile(BaseModel):
    """Editorial targets for a playlist, not clinical or physiological claims."""

    model_config = ConfigDict(extra="forbid")

    intents: list[NeuromentalIntent] = Field(default_factory=list)
    energy_target: int | None = Field(default=None, ge=1, le=5)
    valence_target: int | None = Field(default=None, ge=1, le=5)
    focus_target: int | None = Field(default=None, ge=1, le=5)
    social_energy_target: int | None = Field(default=None, ge=1, le=5)
    sequence_arc: list[SequenceRole] = Field(default_factory=list)
    curation_note: str = Field(default="", max_length=280)


class PublicPlaylistDNA(BaseModel):
    """Configurable editorial DNA for the public playlist network."""

    model_config = ConfigDict(extra="forbid")

    playlist_type: PublicPlaylistType
    genre_vector: dict[str, float] = Field(default_factory=dict)
    subgenre_vector: dict[str, float] = Field(default_factory=dict)
    market_vector: dict[str, float] = Field(default_factory=dict)
    energy_profile: dict[str, float] = Field(default_factory=dict)
    tempo_profile: dict[str, float] = Field(default_factory=dict)
    mood_profile: dict[str, float] = Field(default_factory=dict)
    artist_stage_profile: dict[ArtistStage, float] = Field(default_factory=dict)
    freshness_profile: dict[str, float] = Field(default_factory=dict)
    language_profile: dict[str, float] = Field(default_factory=dict)
    mainstream_discovery_balance: float = Field(default=0.5, ge=0, le=1)
    turnover_min: float = Field(default=0.0, ge=0, le=1)
    turnover_max: float = Field(default=0.0, ge=0, le=1)
    refresh_interval_hours_min: int | None = Field(default=None, ge=1, le=168)
    refresh_interval_hours_max: int | None = Field(default=None, ge=1, le=168)
    max_same_artist: int = Field(default=2, ge=1, le=10)
    max_position_jump: int = Field(default=26, ge=1, le=99)

    @model_validator(mode="after")
    def validate_profile_vectors(self) -> "PublicPlaylistDNA":
        vectors = (
            self.genre_vector,
            self.subgenre_vector,
            self.market_vector,
            self.energy_profile,
            self.tempo_profile,
            self.mood_profile,
            self.artist_stage_profile,
            self.freshness_profile,
            self.language_profile,
        )
        for vector in vectors:
            if any(not math.isfinite(weight) or weight < 0 or weight > 1 for weight in vector.values()):
                raise ValueError("playlist DNA vector weights must be finite values in [0, 1]")
            if vector and not math.isclose(sum(vector.values()), 1.0, abs_tol=0.01):
                raise ValueError("each configured playlist DNA vector must sum to 1.0")
        if self.turnover_max < self.turnover_min:
            raise ValueError("turnover_max must be at least turnover_min")
        if (
            self.refresh_interval_hours_min is not None
            and self.refresh_interval_hours_max is not None
            and self.refresh_interval_hours_max < self.refresh_interval_hours_min
        ):
            raise ValueError("refresh_interval_hours_max must be at least refresh_interval_hours_min")
        return self


class PlaylistConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=300)
    public: bool = True
    target_tracks: int = Field(default=100, ge=0)
    cover: str | None = None
    tracks: list[TrackSpec] = Field(default_factory=list)
    markets: list[str] = Field(default_factory=list)
    rotation: RotationConfig = Field(default_factory=RotationConfig)
    neuromental: NeuromentalProfile | None = None
    world_music: PublicPlaylistDNA | None = None

    @field_validator("markets")
    @classmethod
    def validate_markets(cls, values: list[str]) -> list[str]:
        if any(not re.fullmatch(r"[A-Z]{2}", value) for value in values):
            raise ValueError("markets must be uppercase ISO 3166-1 alpha-2 codes")
        return values


class ResolvedTrack(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    uri: str
    name: str
    artists: list[str] = Field(default_factory=list)
    spotify_url: str | None = None
    isrc: str | None = None
    score: float | None = None


class DiffSummary(BaseModel):
    additions: list[str] = Field(default_factory=list)
    removals: list[str] = Field(default_factory=list)
    moves: int = 0
    unchanged: int = 0
