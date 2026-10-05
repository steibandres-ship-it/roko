from __future__ import annotations

from datetime import date, datetime, timezone
import uuid

from sqlalchemy import CheckConstraint, Date, DateTime, Float, ForeignKey, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return str(uuid.uuid4())


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class IngestionBatch(Base):
    __tablename__ = "ingestion_batches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider_name: Mapped[str] = mapped_column(String(120), nullable=False)
    rights_basis: Mapped[str] = mapped_column(String(20), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(500))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    artists_imported: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    releases_imported: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tracks_imported: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class Market(Base):
    __tablename__ = "markets"
    __table_args__ = (UniqueConstraint("market_key", name="uq_markets_market_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    market_key: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    iso_code: Mapped[str | None] = mapped_column(String(2), unique=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("markets.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    parent: Mapped[Market | None] = relationship(remote_side="Market.id", back_populates="children")
    children: Mapped[list[Market]] = relationship(back_populates="parent")


class Genre(Base):
    __tablename__ = "genres"
    __table_args__ = (UniqueConstraint("slug", name="uq_genres_slug"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    level: Mapped[str] = mapped_column(String(20), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("genres.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    parent: Mapped[Genre | None] = relationship(remote_side="Genre.id", back_populates="children")
    children: Mapped[list[Genre]] = relationship(back_populates="parent")


class Artist(Base):
    __tablename__ = "artists"
    __table_args__ = (Index("ix_artists_name", "name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    country_code: Mapped[str | None] = mapped_column(String(2))
    source_batch_id: Mapped[str] = mapped_column(ForeignKey("ingestion_batches.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)
    identifiers: Mapped[list[ExternalIdentifier]] = relationship(back_populates="artist", cascade="all, delete-orphan")


class Release(Base):
    __tablename__ = "releases"
    __table_args__ = (Index("ix_releases_title", "title"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    release_date: Mapped[date | None] = mapped_column(Date)
    source_batch_id: Mapped[str] = mapped_column(ForeignKey("ingestion_batches.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    identifiers: Mapped[list[ExternalIdentifier]] = relationship(back_populates="release", cascade="all, delete-orphan")


class Track(Base):
    __tablename__ = "tracks"
    __table_args__ = (UniqueConstraint("isrc", name="uq_tracks_isrc"), Index("ix_tracks_title", "title"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    isrc: Mapped[str | None] = mapped_column(String(12))
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    release_id: Mapped[str | None] = mapped_column(ForeignKey("releases.id", ondelete="SET NULL"))
    release_date: Mapped[date | None] = mapped_column(Date)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    source_batch_id: Mapped[str] = mapped_column(ForeignKey("ingestion_batches.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)
    languages: Mapped[list[TrackLanguage]] = relationship(back_populates="track", cascade="all, delete-orphan")
    artists: Mapped[list[TrackArtist]] = relationship(back_populates="track", cascade="all, delete-orphan")
    genres: Mapped[list[TrackGenre]] = relationship(back_populates="track", cascade="all, delete-orphan")
    markets: Mapped[list[TrackMarket]] = relationship(back_populates="track", cascade="all, delete-orphan")
    identifiers: Mapped[list[ExternalIdentifier]] = relationship(back_populates="track", cascade="all, delete-orphan")


class TrackLanguage(Base):
    __tablename__ = "track_languages"
    track_id: Mapped[str] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), primary_key=True)
    language_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    track: Mapped[Track] = relationship(back_populates="languages")


class TrackArtist(Base):
    __tablename__ = "track_artists"
    __table_args__ = (UniqueConstraint("track_id", "artist_id", name="uq_track_artists_pair"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    track_id: Mapped[str] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False)
    artist_id: Mapped[str] = mapped_column(ForeignKey("artists.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    role: Mapped[str] = mapped_column(String(40), nullable=False, default="primary")
    track: Mapped[Track] = relationship(back_populates="artists")
    artist: Mapped[Artist] = relationship()


class ArtistGenre(Base):
    __tablename__ = "artist_genres"
    __table_args__ = (UniqueConstraint("artist_id", "genre_id", name="uq_artist_genres_pair"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    artist_id: Mapped[str] = mapped_column(ForeignKey("artists.id", ondelete="CASCADE"), nullable=False)
    genre_id: Mapped[str] = mapped_column(ForeignKey("genres.id", ondelete="CASCADE"), nullable=False)


class TrackGenre(Base):
    __tablename__ = "track_genres"
    __table_args__ = (
        UniqueConstraint("track_id", "genre_id", name="uq_track_genres_pair"),
        CheckConstraint("weight > 0 AND weight <= 1", name="ck_track_genres_weight"),
        CheckConstraint("data_confidence >= 0 AND data_confidence <= 1", name="ck_track_genres_confidence"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    track_id: Mapped[str] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False)
    genre_id: Mapped[str] = mapped_column(ForeignKey("genres.id", ondelete="CASCADE"), nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    data_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source_batch_id: Mapped[str] = mapped_column(ForeignKey("ingestion_batches.id"), nullable=False)
    track: Mapped[Track] = relationship(back_populates="genres")
    genre: Mapped[Genre] = relationship()


class TrackMarket(Base):
    __tablename__ = "track_markets"
    __table_args__ = (
        UniqueConstraint("track_id", "market_id", name="uq_track_markets_pair"),
        CheckConstraint("data_confidence >= 0 AND data_confidence <= 1", name="ck_track_markets_confidence"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    track_id: Mapped[str] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False)
    market_id: Mapped[str] = mapped_column(ForeignKey("markets.id", ondelete="CASCADE"), nullable=False)
    data_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source_batch_id: Mapped[str] = mapped_column(ForeignKey("ingestion_batches.id"), nullable=False)
    track: Mapped[Track] = relationship(back_populates="markets")
    market: Mapped[Market] = relationship()


class ExternalIdentifier(Base):
    __tablename__ = "external_identifiers"
    __table_args__ = (
        UniqueConstraint("provider", "entity_type", "identifier", name="uq_external_identifiers_identity"),
        CheckConstraint("entity_type IN ('artist', 'release', 'track')", name="ck_external_identifier_entity_type"),
        CheckConstraint(
            "(artist_id IS NOT NULL AND release_id IS NULL AND track_id IS NULL) OR "
            "(artist_id IS NULL AND release_id IS NOT NULL AND track_id IS NULL) OR "
            "(artist_id IS NULL AND release_id IS NULL AND track_id IS NOT NULL)",
            name="ck_external_identifier_one_owner",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    identifier: Mapped[str] = mapped_column(String(160), nullable=False)
    artist_id: Mapped[str | None] = mapped_column(ForeignKey("artists.id", ondelete="CASCADE"))
    release_id: Mapped[str | None] = mapped_column(ForeignKey("releases.id", ondelete="CASCADE"))
    track_id: Mapped[str | None] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"))
    artist: Mapped[Artist | None] = relationship(back_populates="identifiers")
    release: Mapped[Release | None] = relationship(back_populates="identifiers")
    track: Mapped[Track | None] = relationship(back_populates="identifiers")


class MetricSnapshot(Base):
    """One dated, source-attributed observation; null metrics are never stored as zero."""

    __tablename__ = "metric_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "provider_name",
            "entity_type",
            "provider_entity_id",
            "metric_code",
            "platform",
            "market_code",
            "observed_at",
            name="uq_metric_snapshot_observation",
        ),
        CheckConstraint("entity_type IN ('track', 'artist', 'playlist', 'genre', 'market')", name="ck_metric_entity_type"),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_metric_confidence"),
        CheckConstraint("coverage IS NULL OR (coverage >= 0 AND coverage <= 1)", name="ck_metric_coverage"),
        Index("ix_metric_entity_time", "entity_type", "provider_entity_id", "metric_code", "platform", "market_code", "observed_at"),
        Index("ix_metric_market_time", "market_code", "metric_code", "observed_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider_name: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_entity_id: Mapped[str] = mapped_column(String(160), nullable=False)
    canonical_entity_id: Mapped[str | None] = mapped_column(String(36))
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_label: Mapped[str | None] = mapped_column(String(240))
    artist_label: Mapped[str | None] = mapped_column(String(240))
    release_date: Mapped[date | None] = mapped_column(Date)
    metric_code: Mapped[str] = mapped_column(String(80), nullable=False)
    platform: Mapped[str] = mapped_column(String(80), nullable=False, default="unknown")
    market_code: Mapped[str] = mapped_column(String(80), nullable=False, default="GLOBAL")
    genre_slug: Mapped[str | None] = mapped_column(String(100))
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(40), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    # Nullable means the source does not publish a confidence/coverage value.
    confidence: Mapped[float | None] = mapped_column(Float)
    coverage: Mapped[float | None] = mapped_column(Float)
    source_url: Mapped[str | None] = mapped_column(String(500))
    rights_basis: Mapped[str] = mapped_column(String(20), nullable=False)
    sync_run_id: Mapped[str | None] = mapped_column(ForeignKey("provider_sync_runs.id", ondelete="SET NULL"))


class IntelligenceScore(Base):
    """Versioned, explainable score calculated only from recorded observations."""

    __tablename__ = "intelligence_scores"
    __table_args__ = (
        CheckConstraint("score_value IS NULL OR (score_value >= 0 AND score_value <= 100)", name="ck_score_value"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_score_confidence"),
        Index("ix_score_type_market_value", "score_type", "market_code", "score_value"),
        Index("ix_score_entity_time", "entity_type", "provider_entity_id", "calculated_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    score_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_entity_id: Mapped[str] = mapped_column(String(160), nullable=False)
    canonical_entity_id: Mapped[str | None] = mapped_column(String(36))
    entity_label: Mapped[str | None] = mapped_column(String(240))
    artist_label: Mapped[str | None] = mapped_column(String(240))
    market_code: Mapped[str] = mapped_column(String(80), nullable=False, default="GLOBAL")
    genre_slug: Mapped[str | None] = mapped_column(String(100))
    score_value: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence_coverage: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(40), nullable=False)
    feature_version: Mapped[str] = mapped_column(String(40), nullable=False)
    weights_version: Mapped[str] = mapped_column(String(40), nullable=False)
    components: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class ProviderSyncRun(Base):
    __tablename__ = "provider_sync_runs"
    __table_args__ = (
        CheckConstraint("data_confidence >= 0 AND data_confidence <= 1", name="ck_provider_run_confidence"),
        Index("ix_provider_runs_name_started", "provider_name", "started_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider_name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    observations_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    markets_requested: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    metrics_available: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    rate_limit_status: Mapped[str] = mapped_column(String(80), nullable=False, default="UNKNOWN")
    data_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80))


class CrossBorderPropagation(Base):
    __tablename__ = "cross_border_propagation"
    __table_args__ = (
        UniqueConstraint("provider_entity_id", "origin_market", "destination_market", name="uq_cross_border_route"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_propagation_confidence"),
        Index("ix_propagation_destination", "destination_market", "first_detected_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider_entity_id: Mapped[str] = mapped_column(String(160), nullable=False)
    canonical_entity_id: Mapped[str | None] = mapped_column(String(36))
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False, default="track")
    genre_slug: Mapped[str | None] = mapped_column(String(100))
    origin_market: Mapped[str] = mapped_column(String(80), nullable=False)
    destination_market: Mapped[str] = mapped_column(String(80), nullable=False)
    first_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    days_to_propagate: Mapped[int | None] = mapped_column(Integer)
    growth_velocity: Mapped[float | None] = mapped_column(Float)
    acceleration: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)


class OrganicCampaignReport(Base):
    """Operator-entered weekly organic campaign metrics with explicit provenance."""

    __tablename__ = "organic_campaign_reports"
    __table_args__ = (
        UniqueConstraint("playlist_slug", "period_start", "period_end", name="uq_organic_campaign_period"),
        CheckConstraint("period_end >= period_start", name="ck_organic_campaign_period_order"),
        CheckConstraint("views IS NULL OR views >= 0", name="ck_organic_campaign_views_nonnegative"),
        CheckConstraint("reach IS NULL OR reach >= 0", name="ck_organic_campaign_reach_nonnegative"),
        CheckConstraint("link_clicks IS NULL OR link_clicks >= 0", name="ck_organic_campaign_clicks_nonnegative"),
        CheckConstraint("followers_start IS NULL OR followers_start >= 0", name="ck_organic_campaign_followers_start_nonnegative"),
        CheckConstraint("followers_end IS NULL OR followers_end >= 0", name="ck_organic_campaign_followers_end_nonnegative"),
        Index("ix_organic_campaign_reports_playlist_period", "playlist_slug", "period_end"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    playlist_slug: Mapped[str] = mapped_column(String(100), nullable=False)
    channel: Mapped[str] = mapped_column(String(60), nullable=False)
    source_name: Mapped[str] = mapped_column(String(120), nullable=False)
    followers_source: Mapped[str | None] = mapped_column(String(120))
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    views: Mapped[int | None] = mapped_column(Integer)
    reach: Mapped[int | None] = mapped_column(Integer)
    link_clicks: Mapped[int | None] = mapped_column(Integer)
    followers_start: Mapped[int | None] = mapped_column(Integer)
    followers_end: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
