"""Query indexes and backend-only PostgreSQL access."""
from alembic import op
revision = 'f92a206supa01'
down_revision = 'e81f206fan01'
branch_labels = None
depends_on = None
INDEXES = [('ix_wm_markets_parent_id', 'markets', ['parent_id']), ('ix_wm_genres_parent_id', 'genres', ['parent_id']), ('ix_wm_artists_source_batch_id', 'artists', ['source_batch_id']), ('ix_wm_releases_source_batch_id', 'releases', ['source_batch_id']), ('ix_wm_tracks_release_id', 'tracks', ['release_id']), ('ix_wm_tracks_source_batch_id', 'tracks', ['source_batch_id']), ('ix_wm_track_languages_track_id', 'track_languages', ['track_id']), ('ix_wm_track_artists_artist_id', 'track_artists', ['artist_id']), ('ix_wm_artist_genres_genre_id', 'artist_genres', ['genre_id']), ('ix_wm_track_genres_genre_id', 'track_genres', ['genre_id']), ('ix_wm_track_genres_source_batch_id', 'track_genres', ['source_batch_id']), ('ix_wm_track_markets_market_id', 'track_markets', ['market_id']), ('ix_wm_track_markets_source_batch_id', 'track_markets', ['source_batch_id']), ('ix_wm_external_identifiers_artist_id', 'external_identifiers', ['artist_id']), ('ix_wm_external_identifiers_release_id', 'external_identifiers', ['release_id']), ('ix_wm_external_identifiers_track_id', 'external_identifiers', ['track_id']), ('ix_wm_metric_snapshots_sync_run_id', 'metric_snapshots', ['sync_run_id']), ('ix_wm_fan_campaigns_artist_id', 'fan_campaigns', ['artist_id']), ('ix_wm_fan_recipients_contact_id', 'fan_recipients', ['contact_id']), ('ix_fan_audience', 'fan_contacts', ['artist_id', 'status', 'source']), ('ix_fan_queue', 'fan_recipients', ['campaign_id', 'status']), ('ix_metric_provider_market_time', 'metric_snapshots', ['provider_name', 'market_code', 'observed_at'])]
TABLES = ['ingestion_batches', 'markets', 'genres', 'artists', 'releases', 'tracks', 'track_languages', 'track_artists', 'artist_genres', 'track_genres', 'track_markets', 'external_identifiers', 'metric_snapshots', 'intelligence_scores', 'provider_sync_runs', 'cross_border_propagation', 'organic_campaign_reports', 'fan_contacts', 'fan_campaigns', 'fan_recipients', 'alembic_version']

def upgrade():
    for name, table, columns in INDEXES:
        op.create_index(name, table, columns)
    if op.get_bind().dialect.name == "postgresql":
        for table in TABLES:
            op.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'REVOKE ALL ON TABLE public."{table}" FROM PUBLIC')
            for role in ('anon', 'authenticated'):
                op.execute(f'''DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN REVOKE ALL ON TABLE public."{table}" FROM {role}; END IF; END $$''')

def downgrade():
    # Deliberately preserve RLS and denied grants on downgrade.
    for name, table, columns in reversed(INDEXES):
        op.drop_index(name, table_name=table)
