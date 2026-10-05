import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.playlists import load_playlists
from app.world_music.models import Base, IntelligenceScore, MetricSnapshot
from app.world_music.providers.base import ProviderStatus
from app.world_music.public_playlists import _score_evidence_is_usable, generate_public_playlist_drafts, public_playlist_registry_view


def _session() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return Session(engine, autoflush=False, expire_on_commit=False)


def _disconnected_statuses() -> list[ProviderStatus]:
    return [
        ProviderStatus(provider_name=name, state="PROVIDER_NOT_CONNECTED")
        for name in ("Soundcharts", "Chartmetric", "Last.fm")
    ]


def test_all_existing_public_playlists_have_validated_world_music_dna():
    playlists = load_playlists()
    stage_targets = {
        "NOW": {"core": 0.40, "scaling": 0.20, "breakout": 0.20, "emerging": 0.15, "seed": 0.05},
        "RISING": {"elite": 0.10, "scaling": 0.20, "breakout": 0.35, "emerging": 0.30, "seed": 0.05},
        "DISCOVERY": {"scaling": 0.05, "breakout": 0.20, "emerging": 0.45, "seed": 0.30},
        "NEXT": {"breakout": 0.10, "emerging": 0.45, "seed": 0.45},
    }

    assert len(playlists) == 10
    assert all(playlist.public and playlist.world_music is not None for playlist in playlists)
    assert {playlist.world_music.playlist_type for playlist in playlists} <= {
        "NOW", "RISING", "BREAKOUT", "DISCOVERY", "NEXT",
    }
    for playlist in playlists:
        profile = playlist.world_music
        assert profile.turnover_min <= profile.turnover_max
        assert set(profile.market_vector) == set(playlist.markets)
        assert sum(profile.freshness_profile.values()) == pytest.approx(1.0, abs=0.01)
        assert profile.refresh_interval_hours_min == profile.refresh_interval_hours_max == 24
        if profile.playlist_type in stage_targets:
            assert profile.artist_stage_profile == stage_targets[profile.playlist_type]
        assert profile.max_same_artist >= 1


def test_unconnected_providers_produce_blocked_review_only_drafts_without_spotify_writes():
    session = _session()
    try:
        statuses = _disconnected_statuses()
        result = generate_public_playlist_drafts(session, statuses)
        network = public_playlist_registry_view(statuses)

        assert result["draft_only"] is True
        assert result["spotify_writes_enabled"] is False
        assert result["playlist_count"] == 10
        assert result["candidate_count"] == 0
        assert all(item["status"] == "DATA_NOT_AVAILABLE" for item in result["items"])
        assert all(item["publish_state"] == "NOT_PUBLISHED" for item in result["items"])
        assert all(item["stage_mix_applied"] is False for item in result["items"])
        assert network["playlist_count"] == 10
        assert network["data_state"] == "DATA_NOT_AVAILABLE"
        assert all(item["retrievers"] for item in network["items"])
        assert all(
            metric["value"] is None and metric["state"] == "DATA_NOT_AVAILABLE"
            for item in network["items"]
            for metric in item["health_scores"].values()
        )
    finally:
        session.close()


def test_generator_can_scope_a_single_playlist_and_reject_unknown_slug():
    session = _session()
    try:
        result = generate_public_playlist_drafts(
            session,
            _disconnected_statuses(),
            playlist_slug="reggaeton-worldwide",
        )
        assert result["playlist_count"] == 1
        assert result["items"][0]["slug"] == "reggaeton-worldwide"

        with pytest.raises(ValueError, match="Unknown playlist slug"):
            generate_public_playlist_drafts(session, _disconnected_statuses(), playlist_slug="missing-list")
    finally:
        session.close()


def test_playlist_score_requires_fresh_source_evidence_with_matching_provider_rights_and_market():
    session = _session()
    now = datetime.now(timezone.utc)
    score = IntelligenceScore(
        score_type="trend",
        entity_type="track",
        provider_entity_id="chartmetric-track-1",
        market_code="CL",
        score_value=72,
        confidence=0.8,
        evidence_coverage=0.8,
        status="READY",
        algorithm_version="test",
        feature_version="test",
        weights_version="test",
        components={},
        evidence={"source_provider": "Chartmetric"},
        calculated_at=now,
    )
    status = ProviderStatus(provider_name="Chartmetric", state="CONNECTED")
    session.add(MetricSnapshot(
        provider_name="Chartmetric",
        provider_entity_id="chartmetric-track-1",
        entity_type="track",
        metric_code="chart_position",
        platform="spotify",
        market_code="CL",
        value=12,
        unit="rank",
        observed_at=now,
        captured_at=now,
        rights_basis="licensed",
    ))
    session.flush()
    try:
        assert _score_evidence_is_usable(session, score, {"Chartmetric": status}) == (True, None)

        score.calculated_at = now - timedelta(days=4)
        usable, reason = _score_evidence_is_usable(session, score, {"Chartmetric": status})
        assert not usable
        assert reason == "source_evidence_or_score_is_stale"

        disconnected = ProviderStatus(provider_name="Chartmetric", state="PROVIDER_NOT_CONNECTED")
        usable, reason = _score_evidence_is_usable(session, score, {"Chartmetric": disconnected})
        assert not usable
        assert reason == "score_provider_not_connected_and_verified"
    finally:
        session.close()
