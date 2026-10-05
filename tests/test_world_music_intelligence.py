from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.world_music.ingestion import sync_chartmetric
from app.world_music.metrics import Point, calculate_window_metrics, percentile_rank
from app.world_music.models import Base, CrossBorderPropagation, IntelligenceScore, MetricSnapshot, ProviderSyncRun
from app.world_music.providers.chartmetric import (
    ChartmetricFreeTrialRequired,
    ChartmetricProvider,
    ChartmetricResponse,
    ChartmetricRightsNotConfirmed,
)
from app.world_music.providers.soundcharts import SoundchartsAPIError, SoundchartsProvider
from app.world_music.score_engine import recalculate_trend_scores
from app.world_music.scoring import calculate_score


def _factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False), engine


def _configure_chartmetric_free_trial(monkeypatch):
    started = datetime.now(timezone.utc) - timedelta(minutes=1)
    ends = started + timedelta(days=7)
    monkeypatch.setenv("CHARTMETRIC_ACCESS_MODE", "free_7_day_trial")
    monkeypatch.setenv("CHARTMETRIC_FREE_TRIAL_STARTED_AT", started.isoformat())
    monkeypatch.setenv("CHARTMETRIC_FREE_TRIAL_ENDS_AT", ends.isoformat())


class _MemoryProvider:
    def __init__(self, rights_confirmed: bool = True):
        self.rights_confirmed = rights_confirmed
        self.requests_made = 0
        self.limit = None

    def set_request_budget(self, request_budget):
        self.limit = request_budget
        self.requests_made = 0

    def _response(self, payload):
        self.requests_made += 1
        return ChartmetricResponse(payload, 200, 1, "999")

    def fetch_growth_tracks(self, platform_metric, **kwargs):
        return self._response({"obj": [{
            "cm_track": 1001,
            "name": "Real provider title",
            "artist": [{"name": "Artist"}],
            "weekly_diff_percent": {platform_metric: 12.5},
            "album": [{"release_date": "2026-09-22"}],
        }]})

    def fetch_chart(self, platform, country_code, **kwargs):
        observed = "2026-10-01" if country_code in {"GLOBAL", "CL"} else "2026-10-02"
        return self._response({"obj": {"data": [{
            "cm_track": 1001,
            "name": "Real provider title",
            "spotify_artist_names": ["Artist"],
            "rank": 8,
            "rankStats": [{"rank": 8, "timestp": f"{observed}T00:00:00Z"}],
            "album": [{"release_date": "2026-09-22"}],
        }]}})


def test_rank_time_series_keeps_units_and_missing_baseline_explicit():
    points = [
        Point(40, datetime(2026, 10, 1, tzinfo=timezone.utc)),
        Point(20, datetime(2026, 10, 2, tzinfo=timezone.utc)),
    ]
    result = calculate_window_metrics(points, "24h", direction="lower_is_better")
    assert result.absolute_delta == 20
    assert result.velocity_per_day == 20
    assert result.relative_delta == 0.5
    assert result.persistence is None


def test_percentile_requires_comparable_minimum_cohort():
    assert percentile_rank(2, [1, 2, 3], minimum_cohort=20) is None
    assert percentile_rank(2, list(range(1, 21)), minimum_cohort=20) is not None


def test_score_unknown_confidence_is_not_treated_as_full_confidence():
    features = {
        "velocity": 0.8,
        "acceleration": 0.7,
        "cross_platform": 0.5,
        "geo_expansion": 0.6,
        "persistence": 0.8,
        "freshness": 1.0,
    }
    result = calculate_score("trend", features, feature_confidence={key: 0.0 for key in features})
    assert result.value is not None
    assert result.status == "LOW_CONFIDENCE"
    assert result.confidence == 0


def test_chartmetric_refresh_token_is_cached_and_verification_does_not_store_data(monkeypatch):
    _configure_chartmetric_free_trial(monkeypatch)
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/api/token":
            assert json.loads(request.content) == {"refreshtoken": "test-refresh-token"}
            return httpx.Response(200, json={"token": "test-access-token", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer test-access-token"
        return httpx.Response(200, json={"obj": {"id": 206557}}, headers={"X-RateLimit-Remaining": "99"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = ChartmetricProvider("test-refresh-token", rights_confirmed=True, http_client=client)
    try:
        first = provider.verify()
        second = provider.verify()
        assert first.state == second.state == "CONNECTED"
        assert first.rate_limit_status == "RATE_LIMIT_REMAINING:99"
        assert [request.url.path for request in calls] == ["/api/token", "/api/artist/206557", "/api/artist/206557"]
    finally:
        client.close()


def test_chartmetric_rights_gate_blocks_network_requests():
    calls = []
    client = httpx.Client(transport=httpx.MockTransport(lambda request: (calls.append(request), httpx.Response(200, json={"obj": []}))[1]))
    provider = ChartmetricProvider("test-refresh-token", rights_confirmed=False, http_client=client)
    try:
        with pytest.raises(ChartmetricRightsNotConfirmed):
            provider.fetch_chart("spotify", "CL")
        assert calls == []
        assert provider.requests_made == 0
    finally:
        client.close()


def test_chartmetric_trial_window_gate_blocks_network_requests(monkeypatch):
    calls = []
    now = datetime.now(timezone.utc)
    monkeypatch.setenv("CHARTMETRIC_ACCESS_MODE", "free_7_day_trial")
    monkeypatch.setenv("CHARTMETRIC_FREE_TRIAL_STARTED_AT", (now - timedelta(days=8)).isoformat())
    monkeypatch.setenv("CHARTMETRIC_FREE_TRIAL_ENDS_AT", (now - timedelta(days=1)).isoformat())
    client = httpx.Client(transport=httpx.MockTransport(lambda request: (calls.append(request), httpx.Response(200, json={"obj": []}))[1]))
    provider = ChartmetricProvider("test-refresh-token", rights_confirmed=True, http_client=client)
    try:
        with pytest.raises(ChartmetricFreeTrialRequired):
            provider.fetch_chart("spotify", "CL")
        assert calls == []
        assert provider.requests_made == 0
        assert provider.status().rate_limit_status == "FREE_TRIAL_EXPIRED"
    finally:
        client.close()


def test_sync_is_hard_blocked_without_rights_and_writes_no_run():
    factory, engine = _factory()
    try:
        with factory() as session:
            with pytest.raises(ChartmetricRightsNotConfirmed):
                sync_chartmetric(session, _MemoryProvider(False), market_codes=["CL"])
            assert session.scalar(select(ProviderSyncRun.id)) is None
            assert session.scalar(select(MetricSnapshot.id)) is None
    finally:
        engine.dispose()


def test_authorized_sync_stores_scoped_provider_metrics_and_market_routes(monkeypatch):
    _configure_chartmetric_free_trial(monkeypatch)
    factory, engine = _factory()
    try:
        with factory() as session:
            provider = _MemoryProvider()
            run = sync_chartmetric(session, provider, market_codes=["CL", "MX"], max_requests=20)
            assert run.status == "SUCCEEDED"
            assert run.request_count == 10
            assert run.observations_written == 10
            snapshots = session.scalars(select(MetricSnapshot)).all()
            assert all(row.confidence is None and row.coverage is None for row in snapshots)
            assert any(row.unit == "provider_weekly_diff_percent" and row.market_code == "GLOBAL" for row in snapshots)
            assert any(row.unit == "chart_rank" and row.market_code == "CL" for row in snapshots)
            route = session.scalar(select(CrossBorderPropagation))
            assert route is not None
            assert route.origin_market == "CL"
            assert route.destination_market == "MX"
            assert route.days_to_propagate == 1
            assert route.evidence["causality_established"] is False
    finally:
        engine.dispose()


def test_soundcharts_free_trial_budget_stops_requests(monkeypatch):
    calls = []
    monkeypatch.setenv("SOUNDCHARTS_ACCESS_MODE", "free_1000_request_trial")
    client = httpx.Client(transport=httpx.MockTransport(lambda request: (calls.append(request), httpx.Response(200, json={"items": []}))[1]))
    provider = SoundchartsProvider(
        legacy_app_id="test-app",
        legacy_api_key="test-key",
        rights_confirmed=True,
        http_client=client,
    )
    provider.set_request_budget(1)
    try:
        provider.fetch_latest_song_chart("spotify_global", limit=10)
        with pytest.raises(SoundchartsAPIError) as error:
            provider.fetch_latest_song_chart("spotify_cl", limit=10)
        assert error.value.code == "LOCAL_FREE_TRIAL_REQUEST_CAP_REACHED"
        assert len(calls) == 1
        assert provider.requests_made == 1
    finally:
        client.close()


def test_scores_stay_unscored_when_cohorts_and_history_are_insufficient():
    factory, engine = _factory()
    try:
        with factory() as session:
            result = recalculate_trend_scores(session)
            assert result["scores_created"] == 0
            assert result["score_rows_written"] == 0
            assert result["series_evaluated"] == 0
            assert result["scores_by_type"] == {"trend": 0, "breakout": 0, "next": 0}
    finally:
        engine.dispose()


def test_trend_score_requires_comparable_cohort_and_dated_history():
    factory, engine = _factory()
    try:
        with factory() as session:
            now = datetime.now(timezone.utc)
            for track_number in range(20):
                for days_ago in range(15):
                    session.add(MetricSnapshot(
                        provider_name="Chartmetric",
                        provider_entity_id=f"song-{track_number:02d}",
                        entity_type="track",
                        entity_label=f"Track {track_number}",
                        metric_code="chart_position",
                        platform="spotify",
                        market_code="CL",
                        release_date=now.date() - timedelta(days=10),
                        value=float(100 - (15 - days_ago)),
                        unit="chart_rank",
                        observed_at=now - timedelta(days=days_ago),
                        captured_at=now - timedelta(days=days_ago),
                        confidence=None,
                        coverage=None,
                        source_url="https://example.invalid/test-fixture",
                        rights_basis="licensed",
                    ))
                session.add(MetricSnapshot(
                    provider_name="Chartmetric",
                    provider_entity_id=f"song-{track_number:02d}",
                    entity_type="track",
                    entity_label=f"Track {track_number}",
                    release_date=now.date() - timedelta(days=10),
                    metric_code="weekly_growth_percent_spotify_plays",
                    platform="spotify",
                    market_code="GLOBAL",
                    value=float(track_number + 1),
                    unit="provider_weekly_diff_percent",
                    observed_at=now,
                    captured_at=now,
                    confidence=None,
                    coverage=None,
                    source_url="https://example.invalid/test-fixture",
                    rights_basis="licensed",
                ))
            result = recalculate_trend_scores(session)
            session.flush()
            scores = session.scalars(select(IntelligenceScore)).all()
            available = [score for score in scores if score.score_value is not None]
            assert result["scores_by_type"] == {"trend": 20, "breakout": 20, "next": 20}
            assert len(available) == 60
            assert all(score.status == "LOW_CONFIDENCE" for score in available)
            assert all(score.evidence_coverage >= 0.60 for score in available)
            assert all(score.evidence["provider_reported_confidence"] is None for score in available)
    finally:
        engine.dispose()
