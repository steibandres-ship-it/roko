-- WORLD MUSIC OS: fresh database only. Generated from Alembic.
-- Backend-only access; no public API policies.
BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 86796390003b

CREATE TABLE genres (
    id VARCHAR(36) NOT NULL, 
    slug VARCHAR(100) NOT NULL, 
    name VARCHAR(120) NOT NULL, 
    level VARCHAR(20) NOT NULL, 
    parent_id VARCHAR(36), 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(parent_id) REFERENCES genres (id) ON DELETE SET NULL, 
    CONSTRAINT uq_genres_slug UNIQUE (slug)
);

CREATE TABLE ingestion_batches (
    id VARCHAR(36) NOT NULL, 
    provider_name VARCHAR(120) NOT NULL, 
    rights_basis VARCHAR(20) NOT NULL, 
    source_url VARCHAR(500), 
    captured_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    data_confidence FLOAT NOT NULL, 
    content_hash VARCHAR(64) NOT NULL, 
    artists_imported INTEGER NOT NULL, 
    releases_imported INTEGER NOT NULL, 
    tracks_imported INTEGER NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (content_hash)
);

CREATE TABLE markets (
    id VARCHAR(36) NOT NULL, 
    market_key VARCHAR(80) NOT NULL, 
    name VARCHAR(120) NOT NULL, 
    scope VARCHAR(20) NOT NULL, 
    iso_code VARCHAR(2), 
    parent_id VARCHAR(36), 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(parent_id) REFERENCES markets (id) ON DELETE SET NULL, 
    UNIQUE (iso_code), 
    CONSTRAINT uq_markets_market_key UNIQUE (market_key)
);

CREATE TABLE artists (
    id VARCHAR(36) NOT NULL, 
    name VARCHAR(200) NOT NULL, 
    country_code VARCHAR(2), 
    source_batch_id VARCHAR(36) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(source_batch_id) REFERENCES ingestion_batches (id)
);

CREATE INDEX ix_artists_name ON artists (name);

CREATE TABLE releases (
    id VARCHAR(36) NOT NULL, 
    title VARCHAR(240) NOT NULL, 
    release_date DATE, 
    source_batch_id VARCHAR(36) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(source_batch_id) REFERENCES ingestion_batches (id)
);

CREATE INDEX ix_releases_title ON releases (title);

CREATE TABLE artist_genres (
    id VARCHAR(36) NOT NULL, 
    artist_id VARCHAR(36) NOT NULL, 
    genre_id VARCHAR(36) NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(artist_id) REFERENCES artists (id) ON DELETE CASCADE, 
    FOREIGN KEY(genre_id) REFERENCES genres (id) ON DELETE CASCADE, 
    CONSTRAINT uq_artist_genres_pair UNIQUE (artist_id, genre_id)
);

CREATE TABLE tracks (
    id VARCHAR(36) NOT NULL, 
    isrc VARCHAR(12), 
    title VARCHAR(240) NOT NULL, 
    release_id VARCHAR(36), 
    release_date DATE, 
    duration_ms INTEGER, 
    source_batch_id VARCHAR(36) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(release_id) REFERENCES releases (id) ON DELETE SET NULL, 
    FOREIGN KEY(source_batch_id) REFERENCES ingestion_batches (id), 
    CONSTRAINT uq_tracks_isrc UNIQUE (isrc)
);

CREATE INDEX ix_tracks_title ON tracks (title);

CREATE TABLE external_identifiers (
    id VARCHAR(36) NOT NULL, 
    entity_type VARCHAR(20) NOT NULL, 
    provider VARCHAR(80) NOT NULL, 
    identifier VARCHAR(160) NOT NULL, 
    artist_id VARCHAR(36), 
    release_id VARCHAR(36), 
    track_id VARCHAR(36), 
    PRIMARY KEY (id), 
    CONSTRAINT ck_external_identifier_entity_type CHECK (entity_type IN ('artist', 'release', 'track')), 
    CONSTRAINT ck_external_identifier_one_owner CHECK ((artist_id IS NOT NULL AND release_id IS NULL AND track_id IS NULL) OR (artist_id IS NULL AND release_id IS NOT NULL AND track_id IS NULL) OR (artist_id IS NULL AND release_id IS NULL AND track_id IS NOT NULL)), 
    FOREIGN KEY(artist_id) REFERENCES artists (id) ON DELETE CASCADE, 
    FOREIGN KEY(release_id) REFERENCES releases (id) ON DELETE CASCADE, 
    FOREIGN KEY(track_id) REFERENCES tracks (id) ON DELETE CASCADE, 
    CONSTRAINT uq_external_identifiers_identity UNIQUE (provider, entity_type, identifier)
);

CREATE TABLE track_artists (
    id VARCHAR(36) NOT NULL, 
    track_id VARCHAR(36) NOT NULL, 
    artist_id VARCHAR(36) NOT NULL, 
    position INTEGER NOT NULL, 
    role VARCHAR(40) NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(artist_id) REFERENCES artists (id) ON DELETE CASCADE, 
    FOREIGN KEY(track_id) REFERENCES tracks (id) ON DELETE CASCADE, 
    CONSTRAINT uq_track_artists_pair UNIQUE (track_id, artist_id)
);

CREATE TABLE track_genres (
    id VARCHAR(36) NOT NULL, 
    track_id VARCHAR(36) NOT NULL, 
    genre_id VARCHAR(36) NOT NULL, 
    weight FLOAT NOT NULL, 
    data_confidence FLOAT NOT NULL, 
    source_batch_id VARCHAR(36) NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT ck_track_genres_confidence CHECK (data_confidence >= 0 AND data_confidence <= 1), 
    CONSTRAINT ck_track_genres_weight CHECK (weight > 0 AND weight <= 1), 
    FOREIGN KEY(genre_id) REFERENCES genres (id) ON DELETE CASCADE, 
    FOREIGN KEY(source_batch_id) REFERENCES ingestion_batches (id), 
    FOREIGN KEY(track_id) REFERENCES tracks (id) ON DELETE CASCADE, 
    CONSTRAINT uq_track_genres_pair UNIQUE (track_id, genre_id)
);

CREATE TABLE track_languages (
    track_id VARCHAR(36) NOT NULL, 
    language_code VARCHAR(20) NOT NULL, 
    PRIMARY KEY (track_id, language_code), 
    FOREIGN KEY(track_id) REFERENCES tracks (id) ON DELETE CASCADE
);

CREATE TABLE track_markets (
    id VARCHAR(36) NOT NULL, 
    track_id VARCHAR(36) NOT NULL, 
    market_id VARCHAR(36) NOT NULL, 
    data_confidence FLOAT NOT NULL, 
    source_batch_id VARCHAR(36) NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT ck_track_markets_confidence CHECK (data_confidence >= 0 AND data_confidence <= 1), 
    FOREIGN KEY(market_id) REFERENCES markets (id) ON DELETE CASCADE, 
    FOREIGN KEY(source_batch_id) REFERENCES ingestion_batches (id), 
    FOREIGN KEY(track_id) REFERENCES tracks (id) ON DELETE CASCADE, 
    CONSTRAINT uq_track_markets_pair UNIQUE (track_id, market_id)
);

INSERT INTO alembic_version (version_num) VALUES ('86796390003b') RETURNING alembic_version.version_num;

-- Running upgrade 86796390003b -> b6a41d9f2e70

CREATE TABLE provider_sync_runs (
    id VARCHAR(36) NOT NULL, 
    provider_name VARCHAR(120) NOT NULL, 
    status VARCHAR(24) NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    finished_at TIMESTAMP WITH TIME ZONE, 
    request_count INTEGER NOT NULL, 
    observations_written INTEGER NOT NULL, 
    markets_requested JSON NOT NULL, 
    metrics_available JSON NOT NULL, 
    rate_limit_status VARCHAR(80) NOT NULL, 
    data_confidence FLOAT NOT NULL, 
    error_code VARCHAR(80), 
    PRIMARY KEY (id), 
    CONSTRAINT ck_provider_run_confidence CHECK (data_confidence >= 0 AND data_confidence <= 1)
);

CREATE INDEX ix_provider_runs_name_started ON provider_sync_runs (provider_name, started_at);

CREATE TABLE metric_snapshots (
    id VARCHAR(36) NOT NULL, 
    provider_name VARCHAR(120) NOT NULL, 
    provider_entity_id VARCHAR(160) NOT NULL, 
    canonical_entity_id VARCHAR(36), 
    entity_type VARCHAR(20) NOT NULL, 
    entity_label VARCHAR(240), 
    artist_label VARCHAR(240), 
    metric_code VARCHAR(80) NOT NULL, 
    platform VARCHAR(80) NOT NULL, 
    market_code VARCHAR(80) NOT NULL, 
    genre_slug VARCHAR(100), 
    value FLOAT NOT NULL, 
    unit VARCHAR(40) NOT NULL, 
    observed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    captured_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    confidence FLOAT, 
    coverage FLOAT, 
    source_url VARCHAR(500), 
    rights_basis VARCHAR(20) NOT NULL, 
    sync_run_id VARCHAR(36), 
    PRIMARY KEY (id), 
    CONSTRAINT ck_metric_entity_type CHECK (entity_type IN ('track', 'artist', 'playlist', 'genre', 'market')), 
    CONSTRAINT ck_metric_confidence CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)), 
    CONSTRAINT ck_metric_coverage CHECK (coverage IS NULL OR (coverage >= 0 AND coverage <= 1)), 
    FOREIGN KEY(sync_run_id) REFERENCES provider_sync_runs (id) ON DELETE SET NULL, 
    CONSTRAINT uq_metric_snapshot_observation UNIQUE (provider_name, entity_type, provider_entity_id, metric_code, platform, market_code, observed_at)
);

CREATE INDEX ix_metric_entity_time ON metric_snapshots (entity_type, provider_entity_id, metric_code, platform, market_code, observed_at);

CREATE INDEX ix_metric_market_time ON metric_snapshots (market_code, metric_code, observed_at);

CREATE TABLE intelligence_scores (
    id VARCHAR(36) NOT NULL, 
    score_type VARCHAR(40) NOT NULL, 
    entity_type VARCHAR(20) NOT NULL, 
    provider_entity_id VARCHAR(160) NOT NULL, 
    canonical_entity_id VARCHAR(36), 
    entity_label VARCHAR(240), 
    artist_label VARCHAR(240), 
    market_code VARCHAR(80) NOT NULL, 
    genre_slug VARCHAR(100), 
    score_value FLOAT NOT NULL, 
    confidence FLOAT NOT NULL, 
    evidence_coverage FLOAT NOT NULL, 
    status VARCHAR(24) NOT NULL, 
    algorithm_version VARCHAR(40) NOT NULL, 
    feature_version VARCHAR(40) NOT NULL, 
    weights_version VARCHAR(40) NOT NULL, 
    components JSON NOT NULL, 
    evidence JSON NOT NULL, 
    calculated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT ck_score_value CHECK (score_value >= 0 AND score_value <= 100), 
    CONSTRAINT ck_score_confidence CHECK (confidence >= 0 AND confidence <= 1)
);

CREATE INDEX ix_score_type_market_value ON intelligence_scores (score_type, market_code, score_value);

CREATE INDEX ix_score_entity_time ON intelligence_scores (entity_type, provider_entity_id, calculated_at);

CREATE TABLE cross_border_propagation (
    id VARCHAR(36) NOT NULL, 
    provider_entity_id VARCHAR(160) NOT NULL, 
    canonical_entity_id VARCHAR(36), 
    entity_type VARCHAR(20) NOT NULL, 
    genre_slug VARCHAR(100), 
    origin_market VARCHAR(80) NOT NULL, 
    destination_market VARCHAR(80) NOT NULL, 
    first_detected_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    days_to_propagate INTEGER, 
    growth_velocity FLOAT, 
    acceleration FLOAT, 
    confidence FLOAT NOT NULL, 
    evidence JSON NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT ck_propagation_confidence CHECK (confidence >= 0 AND confidence <= 1), 
    CONSTRAINT uq_cross_border_route UNIQUE (provider_entity_id, origin_market, destination_market)
);

CREATE INDEX ix_propagation_destination ON cross_border_propagation (destination_market, first_detected_at);

UPDATE alembic_version SET version_num='b6a41d9f2e70' WHERE alembic_version.version_num = '86796390003b';

-- Running upgrade b6a41d9f2e70 -> c843b1239ae1

ALTER TABLE metric_snapshots ADD COLUMN release_date DATE;

ALTER TABLE intelligence_scores ALTER COLUMN score_value DROP NOT NULL;

UPDATE alembic_version SET version_num='c843b1239ae1' WHERE alembic_version.version_num = 'b6a41d9f2e70';

-- Running upgrade c843b1239ae1 -> d74e19ac6f21

CREATE TABLE organic_campaign_reports (
    id VARCHAR(36) NOT NULL, 
    playlist_slug VARCHAR(100) NOT NULL, 
    channel VARCHAR(60) NOT NULL, 
    source_name VARCHAR(120) NOT NULL, 
    followers_source VARCHAR(120), 
    period_start DATE NOT NULL, 
    period_end DATE NOT NULL, 
    views INTEGER, 
    reach INTEGER, 
    link_clicks INTEGER, 
    followers_start INTEGER, 
    followers_end INTEGER, 
    notes VARCHAR(500), 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT ck_organic_campaign_period_order CHECK (period_end >= period_start), 
    CONSTRAINT ck_organic_campaign_views_nonnegative CHECK (views IS NULL OR views >= 0), 
    CONSTRAINT ck_organic_campaign_reach_nonnegative CHECK (reach IS NULL OR reach >= 0), 
    CONSTRAINT ck_organic_campaign_clicks_nonnegative CHECK (link_clicks IS NULL OR link_clicks >= 0), 
    CONSTRAINT ck_organic_campaign_followers_start_nonnegative CHECK (followers_start IS NULL OR followers_start >= 0), 
    CONSTRAINT ck_organic_campaign_followers_end_nonnegative CHECK (followers_end IS NULL OR followers_end >= 0), 
    CONSTRAINT uq_organic_campaign_period UNIQUE (playlist_slug, period_start, period_end)
);

CREATE INDEX ix_organic_campaign_reports_playlist_period ON organic_campaign_reports (playlist_slug, period_end);

UPDATE alembic_version SET version_num='d74e19ac6f21' WHERE alembic_version.version_num = 'c843b1239ae1';

-- Running upgrade d74e19ac6f21 -> e81f206fan01

CREATE TABLE fan_contacts (
    id VARCHAR(36) NOT NULL, 
    artist_id VARCHAR(36) NOT NULL, 
    email VARCHAR(254) NOT NULL, 
    name VARCHAR(120) NOT NULL, 
    source VARCHAR(240) NOT NULL, 
    consent_at VARCHAR(40) NOT NULL, 
    status VARCHAR(20) NOT NULL, 
    token VARCHAR(64) NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (token), 
    UNIQUE (artist_id, email), 
    FOREIGN KEY(artist_id) REFERENCES artists (id)
);

CREATE TABLE fan_campaigns (
    id VARCHAR(36) NOT NULL, 
    artist_id VARCHAR(36) NOT NULL, 
    subject VARCHAR(200) NOT NULL, 
    body TEXT NOT NULL, 
    source VARCHAR(240) NOT NULL, 
    status VARCHAR(20) NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(artist_id) REFERENCES artists (id)
);

CREATE TABLE fan_recipients (
    id VARCHAR(36) NOT NULL, 
    campaign_id VARCHAR(36) NOT NULL, 
    contact_id VARCHAR(36) NOT NULL, 
    status VARCHAR(20) NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (campaign_id, contact_id), 
    FOREIGN KEY(campaign_id) REFERENCES fan_campaigns (id), 
    FOREIGN KEY(contact_id) REFERENCES fan_contacts (id)
);

UPDATE alembic_version SET version_num='e81f206fan01' WHERE alembic_version.version_num = 'd74e19ac6f21';

-- Running upgrade e81f206fan01 -> f92a206supa01

CREATE INDEX ix_wm_markets_parent_id ON markets (parent_id);

CREATE INDEX ix_wm_genres_parent_id ON genres (parent_id);

CREATE INDEX ix_wm_artists_source_batch_id ON artists (source_batch_id);

CREATE INDEX ix_wm_releases_source_batch_id ON releases (source_batch_id);

CREATE INDEX ix_wm_tracks_release_id ON tracks (release_id);

CREATE INDEX ix_wm_tracks_source_batch_id ON tracks (source_batch_id);

CREATE INDEX ix_wm_track_languages_track_id ON track_languages (track_id);

CREATE INDEX ix_wm_track_artists_artist_id ON track_artists (artist_id);

CREATE INDEX ix_wm_artist_genres_genre_id ON artist_genres (genre_id);

CREATE INDEX ix_wm_track_genres_genre_id ON track_genres (genre_id);

CREATE INDEX ix_wm_track_genres_source_batch_id ON track_genres (source_batch_id);

CREATE INDEX ix_wm_track_markets_market_id ON track_markets (market_id);

CREATE INDEX ix_wm_track_markets_source_batch_id ON track_markets (source_batch_id);

CREATE INDEX ix_wm_external_identifiers_artist_id ON external_identifiers (artist_id);

CREATE INDEX ix_wm_external_identifiers_release_id ON external_identifiers (release_id);

CREATE INDEX ix_wm_external_identifiers_track_id ON external_identifiers (track_id);

CREATE INDEX ix_wm_metric_snapshots_sync_run_id ON metric_snapshots (sync_run_id);

CREATE INDEX ix_wm_fan_campaigns_artist_id ON fan_campaigns (artist_id);

CREATE INDEX ix_wm_fan_recipients_contact_id ON fan_recipients (contact_id);

CREATE INDEX ix_fan_audience ON fan_contacts (artist_id, status, source);

CREATE INDEX ix_fan_queue ON fan_recipients (campaign_id, status);

CREATE INDEX ix_metric_provider_market_time ON metric_snapshots (provider_name, market_code, observed_at);

ALTER TABLE public."ingestion_batches" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public."ingestion_batches" FROM PUBLIC;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON TABLE public."ingestion_batches" FROM anon; END IF; END $$;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON TABLE public."ingestion_batches" FROM authenticated; END IF; END $$;

ALTER TABLE public."markets" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public."markets" FROM PUBLIC;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON TABLE public."markets" FROM anon; END IF; END $$;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON TABLE public."markets" FROM authenticated; END IF; END $$;

ALTER TABLE public."genres" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public."genres" FROM PUBLIC;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON TABLE public."genres" FROM anon; END IF; END $$;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON TABLE public."genres" FROM authenticated; END IF; END $$;

ALTER TABLE public."artists" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public."artists" FROM PUBLIC;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON TABLE public."artists" FROM anon; END IF; END $$;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON TABLE public."artists" FROM authenticated; END IF; END $$;

ALTER TABLE public."releases" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public."releases" FROM PUBLIC;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON TABLE public."releases" FROM anon; END IF; END $$;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON TABLE public."releases" FROM authenticated; END IF; END $$;

ALTER TABLE public."tracks" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public."tracks" FROM PUBLIC;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON TABLE public."tracks" FROM anon; END IF; END $$;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON TABLE public."tracks" FROM authenticated; END IF; END $$;

ALTER TABLE public."track_languages" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public."track_languages" FROM PUBLIC;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON TABLE public."track_languages" FROM anon; END IF; END $$;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON TABLE public."track_languages" FROM authenticated; END IF; END $$;

ALTER TABLE public."track_artists" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public."track_artists" FROM PUBLIC;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON TABLE public."track_artists" FROM anon; END IF; END $$;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON TABLE public."track_artists" FROM authenticated; END IF; END $$;

ALTER TABLE public."artist_genres" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public."artist_genres" FROM PUBLIC;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON TABLE public."artist_genres" FROM anon; END IF; END $$;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON TABLE public."artist_genres" FROM authenticated; END IF; END $$;

ALTER TABLE public."track_genres" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public."track_genres" FROM PUBLIC;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON TABLE public."track_genres" FROM anon; END IF; END $$;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON TABLE public."track_genres" FROM authenticated; END IF; END $$;

ALTER TABLE public."track_markets" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public."track_markets" FROM PUBLIC;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON TABLE public."track_markets" FROM anon; END IF; END $$;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON TABLE public."track_markets" FROM authenticated; END IF; END $$;

ALTER TABLE public."external_identifiers" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public."external_identifiers" FROM PUBLIC;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON TABLE public."external_identifiers" FROM anon; END IF; END $$;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON TABLE public."external_identifiers" FROM authenticated; END IF; END $$;

ALTER TABLE public."metric_snapshots" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public."metric_snapshots" FROM PUBLIC;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON TABLE public."metric_snapshots" FROM anon; END IF; END $$;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON TABLE public."metric_snapshots" FROM authenticated; END IF; END $$;

ALTER TABLE public."intelligence_scores" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public."intelligence_scores" FROM PUBLIC;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON TABLE public."intelligence_scores" FROM anon; END IF; END $$;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON TABLE public."intelligence_scores" FROM authenticated; END IF; END $$;

ALTER TABLE public."provider_sync_runs" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public."provider_sync_runs" FROM PUBLIC;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON TABLE public."provider_sync_runs" FROM anon; END IF; END $$;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON TABLE public."provider_sync_runs" FROM authenticated; END IF; END $$;

ALTER TABLE public."cross_border_propagation" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public."cross_border_propagation" FROM PUBLIC;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON TABLE public."cross_border_propagation" FROM anon; END IF; END $$;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON TABLE public."cross_border_propagation" FROM authenticated; END IF; END $$;

ALTER TABLE public."organic_campaign_reports" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public."organic_campaign_reports" FROM PUBLIC;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON TABLE public."organic_campaign_reports" FROM anon; END IF; END $$;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON TABLE public."organic_campaign_reports" FROM authenticated; END IF; END $$;

ALTER TABLE public."fan_contacts" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public."fan_contacts" FROM PUBLIC;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON TABLE public."fan_contacts" FROM anon; END IF; END $$;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON TABLE public."fan_contacts" FROM authenticated; END IF; END $$;

ALTER TABLE public."fan_campaigns" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public."fan_campaigns" FROM PUBLIC;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON TABLE public."fan_campaigns" FROM anon; END IF; END $$;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON TABLE public."fan_campaigns" FROM authenticated; END IF; END $$;

ALTER TABLE public."fan_recipients" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public."fan_recipients" FROM PUBLIC;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON TABLE public."fan_recipients" FROM anon; END IF; END $$;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON TABLE public."fan_recipients" FROM authenticated; END IF; END $$;

ALTER TABLE public."alembic_version" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public."alembic_version" FROM PUBLIC;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON TABLE public."alembic_version" FROM anon; END IF; END $$;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON TABLE public."alembic_version" FROM authenticated; END IF; END $$;

UPDATE alembic_version SET version_num='f92a206supa01' WHERE alembic_version.version_num = 'e81f206fan01';

-- Running upgrade f92a206supa01 -> a03preusers01

CREATE TABLE preuser_imports (
    id VARCHAR(36) NOT NULL, 
    filename VARCHAR(255) NOT NULL, 
    file_hash VARCHAR(64) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    rows INTEGER NOT NULL, 
    imported INTEGER NOT NULL, 
    duplicates INTEGER NOT NULL, 
    invalid INTEGER NOT NULL, 
    errors JSON NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_preuser_imports_file_hash ON preuser_imports (file_hash);

CREATE TABLE preusers (
    id VARCHAR(36) NOT NULL, 
    email VARCHAR(254) NOT NULL, 
    status VARCHAR(24) NOT NULL, 
    import_id VARCHAR(36) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (email), 
    FOREIGN KEY(import_id) REFERENCES preuser_imports (id)
);

CREATE INDEX ix_preusers_status ON preusers (status);

CREATE INDEX ix_preusers_import_id ON preusers (import_id);

ALTER TABLE public."preusers" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public."preusers" FROM PUBLIC;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON TABLE public."preusers" FROM anon; END IF; END $$;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON TABLE public."preusers" FROM authenticated; END IF; END $$;

ALTER TABLE public."preuser_imports" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public."preuser_imports" FROM PUBLIC;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON TABLE public."preuser_imports" FROM anon; END IF; END $$;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON TABLE public."preuser_imports" FROM authenticated; END IF; END $$;

UPDATE alembic_version SET version_num='a03preusers01' WHERE alembic_version.version_num = 'f92a206supa01';

COMMIT;

