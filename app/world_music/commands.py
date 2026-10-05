from __future__ import annotations

import json
import os
from pathlib import Path

import typer
from alembic import command
from alembic.config import Config
from dotenv import dotenv_values, set_key
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table
import uvicorn
from sqlalchemy.exc import SQLAlchemyError

from ..config import DATA_DIR, PROJECT_ROOT
from ..playlists import load_playlists
from ..storage import JsonStore
from .catalog import import_catalog_batch
from .db import SessionFactory, database_url
from .models import Artist, Genre, IngestionBatch, IntelligenceScore, Market, MetricSnapshot, ProviderSyncRun, Release, Track, now_utc
from .providers.registry import ProviderRegistry
from .providers.chartmetric import ChartmetricAPIError, ChartmetricFreeTrialRequired, ChartmetricProvider, ChartmetricRightsNotConfirmed
from .providers.lastfm import LastFmAPIError, LastFmProvider, LastFmScopeNotConfirmed
from .providers.soundcharts import SoundchartsAPIError, SoundchartsNotConfigured, SoundchartsProvider, SoundchartsRightsNotConfirmed
from .ingestion import sync_chartmetric as run_chartmetric_sync
from .schemas import CatalogImportInput
from .lastfm_ingestion import sync_lastfm
from .score_engine import recalculate_trend_scores
from .soundcharts_ingestion import sync_soundcharts_charts
from .public_playlists import generate_public_playlist_drafts
from .playlist_launch import MAX_NEW_PLAYLISTS_PER_RUN, build_blueprint_launch_plans, plan_public_view, publish_ready_blueprints
from .provider_ingest import merge_provider_sync_statuses, soundcharts_free_calls_remaining


console = Console()
world_app = typer.Typer(no_args_is_help=True, help="World Music OS catalog, providers, database, and API.")


def _store_soundcharts_verification(last_verified_at, quota_remaining: str | None) -> None:
    """Persist non-secret verification metadata for local status views."""
    env_path = str(PROJECT_ROOT / ".env")
    set_key(env_path, "SOUNDCHARTS_API_VERIFIED_AT", last_verified_at.isoformat() if last_verified_at else "")
    set_key(env_path, "SOUNDCHARTS_API_QUOTA_REMAINING", quota_remaining or "")


@world_app.command("db-upgrade")
def db_upgrade() -> None:
    """Create or upgrade the World Music OS database with Alembic migrations."""
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    url = database_url()
    rendered = url.render_as_string(hide_password=False) if hasattr(url, "render_as_string") else str(url)
    config.set_main_option("sqlalchemy.url", rendered.replace("%", "%%"))
    try:
        command.upgrade(config, "head")
    except SQLAlchemyError as exc:
        console.print(f"[red]Database migration failed:[/red] {type(exc).__name__}")
        raise typer.Exit(1) from exc
    console.print("[green]World Music OS schema is at the latest migration.[/green]")


@world_app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind locally by default; expose publicly only behind authentication."),
    port: int = typer.Option(8000, min=1, max=65535),
) -> None:
    """Start the local API; provider writes require the authenticated bridge route."""
    uvicorn.run("app.world_music.api:app", host=host, port=port, reload=False)


@world_app.command("ingest")
def ingest(path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True)) -> None:
    """Import a JSON catalog batch whose metadata is owned, consented, or licensed."""
    if path.stat().st_size > 25 * 1024 * 1024:
        raise typer.BadParameter("catalog import files are limited to 25 MB")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        batch = CatalogImportInput.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        console.print(f"[red]Catalog import rejected:[/red] {exc}")
        raise typer.Exit(2) from exc
    try:
        with SessionFactory() as session:
            result = import_catalog_batch(session, batch)
    except ValueError as exc:
        console.print(f"[red]Catalog identity conflict:[/red] {exc}")
        raise typer.Exit(2) from exc
    except SQLAlchemyError as exc:
        console.print(f"[red]Catalog import failed at the database layer:[/red] {type(exc).__name__}")
        console.print("Run `python -m app world db-upgrade` and check the database connection.")
        raise typer.Exit(1) from exc
    console.print(
        f"Import {result['batch_id']}: {result['artists']} artists, {result['releases']} releases, "
        f"{result['tracks']} tracks; duplicate batch={str(result['duplicate_batch']).lower()}"
    )


@world_app.command("status")
def status() -> None:
    """Show catalog counts and real provider connection states."""
    try:
        with SessionFactory() as session:
            values = {
                "Artists": session.query(Artist).count(),
                "Releases": session.query(Release).count(),
                "Tracks": session.query(Track).count(),
                "Genres": session.query(Genre).count(),
                "Markets": session.query(Market).count(),
                "Imported batches": session.query(IngestionBatch).count(),
                "Soundcharts observations": session.query(MetricSnapshot).filter_by(provider_name="Soundcharts").count(),
                "Chartmetric observations": session.query(MetricSnapshot).filter_by(provider_name="Chartmetric").count(),
                "Last.fm observations": session.query(MetricSnapshot).filter_by(provider_name="Last.fm").count(),
            }
            provider_statuses = merge_provider_sync_statuses(session, ProviderRegistry().statuses())
    except SQLAlchemyError as exc:
        console.print(f"[red]World Music OS database unavailable:[/red] {type(exc).__name__}")
        console.print("Run `python -m app world db-upgrade` or check WORLD_MUSIC_DATABASE_URL.")
        raise typer.Exit(1) from exc
    table = Table(title="WORLD MUSIC OS — Intelligence")
    table.add_column("Catalog")
    table.add_column("Count", justify="right")
    for key, value in values.items():
        table.add_row(key, str(value))
    console.print(table)
    soundcharts_observations = values["Soundcharts observations"]
    for provider in provider_statuses:
        console.print(f"{provider.provider_name}: [yellow]{provider.state}[/yellow]")
        if provider.provider_name == "Soundcharts" and provider.state == "CONNECTED":
            console.print(
                f"  API authentication checked: {provider.last_refresh.isoformat() if provider.last_refresh else 'unknown'}; "
                f"{provider.rate_limit_status}"
            )
            if soundcharts_observations == 0:
                console.print("  No Soundcharts music metrics have been ingested yet.")
        elif provider.last_refresh:
            console.print(f"  Last bridge sync: {provider.last_refresh.isoformat()}; metrics: {', '.join(provider.metrics_available)}")
        if provider.setup_action:
            console.print(f"  [dim]{provider.setup_action}[/dim]")
    console.print("Los datos del panel se mantienen atribuidos a su proveedor; no se mezclan métricas incompatibles ni se garantiza posicionamiento en Spotify.")


@world_app.command("provider-check")
def provider_check() -> None:
    """Verify Last.fm credentials with a small read-only chart request."""
    provider = LastFmProvider.from_environment()
    if not provider.credentials_configured:
        console.print("[yellow]Last.fm API key not configured.[/yellow] Create a free key and set LASTFM_API_KEY in the local .env file.")
        raise typer.Exit(2)
    if not provider.scope_confirmed:
        console.print("[yellow]No request made.[/yellow] Review Last.fm API terms, then set LASTFM_NONCOMMERCIAL_USE=true and LASTFM_NON_EEA_PERMISSION_CONFIRMED=true only if the stated scope is confirmed.")
        raise typer.Exit(2)
    try:
        result = provider.verify()
    except LastFmAPIError as exc:
        console.print(f"[red]Last.fm verification failed:[/red] {exc.code}. Check the API key and provider availability.")
        raise typer.Exit(1) from exc
    finally:
        provider.close()
    console.print(f"Last.fm: [green]{result.state}[/green]")
    console.print(f"Read-only check: {', '.join(result.metrics_available)}; quota/rate status: {result.rate_limit_status}")


def _record_soundcharts_catalog_usage(request_count: int, *, succeeded: bool, error_code: str | None = None) -> None:
    if request_count <= 0:
        return
    finished = now_utc()
    with SessionFactory() as session:
        session.add(ProviderSyncRun(
            provider_name="Soundcharts",
            status="SUCCEEDED" if succeeded else "FAILED",
            started_at=finished,
            finished_at=finished,
            request_count=request_count,
            observations_written=0,
            markets_requested=[],
            metrics_available=["chart_catalog_metadata"] if succeeded else [],
            rate_limit_status="FREE_TRIAL_CATALOG_DISCOVERY",
            data_confidence=0.0,
            error_code=error_code,
        ))
        session.commit()


@world_app.command("sync")
def sync_metrics(
    markets: str | None = typer.Option(None, help="Comma-separated Last.fm country chart markets (defaults to LASTFM_MARKETS)."),
    limit: int = typer.Option(100, min=1, max=100, help="Maximum ranked songs fetched per provider query."),
    max_requests: int = typer.Option(40, min=1, max=100, help="Hard API request budget for this sync."),
) -> None:
    """Capture Last.fm global and country charts for the local dashboard."""
    provider = LastFmProvider.from_environment()
    if not provider.credentials_configured:
        console.print("[yellow]No data fetched.[/yellow] Configure LASTFM_API_KEY in the local .env file first.")
        raise typer.Exit(2)
    if not provider.scope_confirmed:
        provider.close()
        console.print("[yellow]No data fetched or stored.[/yellow] Review Last.fm API terms; confirm non-commercial use and the terms for use outside the EEA before enabling both LASTFM scope flags in .env.")
        raise typer.Exit(2)
    try:
        project_env = dotenv_values(PROJECT_ROOT / ".env")
        market_list = markets or os.environ.get("LASTFM_MARKETS") or project_env.get("LASTFM_MARKETS") or "CL,MX,AR,CO,ES,US,BR"
        with SessionFactory() as session:
            run = sync_lastfm(
                session,
                provider,
                market_codes=[code.strip() for code in str(market_list).split(",") if code.strip()],
                limit=limit,
                max_requests=max_requests,
            )
            session.commit()
    except LastFmScopeNotConfirmed as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(2) from exc
    except SQLAlchemyError as exc:
        console.print(f"[red]Provider sync was not committed:[/red] database error {type(exc).__name__}")
        raise typer.Exit(1) from exc
    finally:
        provider.close()
    color = "green" if run.status == "SUCCEEDED" else "yellow" if run.status in {"PARTIAL", "SKIPPED"} else "red"
    console.print(f"Last.fm sync: [{color}]{run.status}[/{color}]")
    if run.status == "SKIPPED":
        console.print("Ya existe una captura de cada mercado solicitado para el día actual de Santiago.")
    console.print(f"Requests: {run.request_count}; observations: {run.observations_written}; markets: {', '.join(run.markets_requested) or 'none'}")
    console.print("Last.fm rank/playcount metrics are available in /dashboard. Rank movement appears after a previous capture; Spotify stream and playlist metrics are not provided by this source.")
    if run.error_code:
        console.print(f"Provider/plan report: {run.error_code}")


@world_app.command("soundcharts-check")
def soundcharts_check() -> None:
    """Verify Soundcharts API authentication and quota without reading music data."""
    provider = SoundchartsProvider.from_environment()
    if provider is None or not provider.credentials_configured:
        console.print("[yellow]Soundcharts API credentials are not configured.[/yellow] The web dashboard login is separate from API access.")
        console.print("Create API credentials in the Soundcharts developer console and set SOUNDCHARTS_CLIENT_ID and SOUNDCHARTS_CLIENT_SECRET in .env.")
        raise typer.Exit(2)
    try:
        result = provider.verify()
    except SoundchartsAPIError as exc:
        if exc.status_code in {401, 403}:
            _store_soundcharts_verification(None, None)
        console.print(f"[red]Soundcharts API check failed:[/red] {exc.status_code} {exc.code}. Check credentials and API access.")
        raise typer.Exit(1) from exc
    except SoundchartsNotConfigured as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(2) from exc
    finally:
        provider.close()
    try:
        _store_soundcharts_verification(result.last_refresh, provider.quota_remaining)
    except OSError:
        console.print("[yellow]Authentication verified, but local status metadata could not be saved to .env.[/yellow]")
    console.print(f"Soundcharts API authentication: [green]verified[/green]")
    console.print(f"Music-data scope: [yellow]{result.state}[/yellow]")
    console.print(f"Quota/rate report: {result.rate_limit_status}")
    if result.setup_action:
        console.print(result.setup_action)


@world_app.command("soundcharts-charts")
def soundcharts_charts(
    platform: str = typer.Option("spotify", help="Soundcharts chart platform code, such as spotify."),
    country_code: str | None = typer.Option(None, help="Optional ISO country code; omit for global charts."),
    limit: int = typer.Option(100, min=1, max=100),
) -> None:
    """List official Soundcharts chart slugs for explicit editorial selection."""
    provider = SoundchartsProvider.from_environment()
    if provider is None or not provider.credentials_configured:
        console.print("[yellow]No chart data fetched.[/yellow] Configure Soundcharts API credentials first.")
        raise typer.Exit(2)
    if not provider.rights_confirmed:
        console.print("[yellow]No chart data fetched.[/yellow] Written rights for reading, local snapshots, and playlist research are required.")
        raise typer.Exit(2)
    with SessionFactory() as session:
        remaining = soundcharts_free_calls_remaining(session)
    if remaining <= 0:
        provider.close()
        console.print("[yellow]No catalog request made.[/yellow] The local 1,000-request free-trial budget is exhausted; paid access is disabled.")
        raise typer.Exit(2)
    provider.set_request_budget(min(remaining, 1))
    succeeded = False
    try:
        payload = provider.fetch_song_chart_catalog(platform, country_code=country_code, limit=limit)
        succeeded = True
    except SoundchartsNotConfigured as exc:
        console.print(f"[yellow]No Soundcharts request made:[/yellow] {exc}")
        raise typer.Exit(2) from exc
    except SoundchartsAPIError as exc:
        console.print(f"[red]Soundcharts chart catalog failed:[/red] {exc.status_code} {exc.code}")
        _record_soundcharts_catalog_usage(provider.requests_made, succeeded=False, error_code=exc.code)
        raise typer.Exit(1) from exc
    finally:
        provider.close()
    _record_soundcharts_catalog_usage(provider.requests_made, succeeded=succeeded)
    rows = payload.get("items", [])
    table = Table(title=f"Soundcharts charts · {platform} · {country_code or 'global/catalog'}")
    for heading in ("Name", "Slug", "Country", "Type", "Frequency"):
        table.add_column(heading)
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                table.add_row(*(str(row.get(key) or "—") for key in ("name", "slug", "countryCode", "type", "frequency")))
    console.print(table)
    console.print(f"Requests in this command: {provider.requests_made}; quota remaining: {provider.quota_remaining or 'not reported'}")


@world_app.command("soundcharts-sync")
def soundcharts_sync(
    charts: str = typer.Option(..., help="Comma-separated exact chart slugs from `world soundcharts-charts`."),
    limit: int = typer.Option(100, min=1, max=100),
    max_requests: int = typer.Option(10, min=1, max=100, help="Hard cap; at least the number of selected chart slugs."),
) -> None:
    """Store normalized ranking observations from explicitly selected Soundcharts charts."""
    provider = SoundchartsProvider.from_environment()
    if provider is None or not provider.credentials_configured:
        console.print("[yellow]No data fetched.[/yellow] Configure Soundcharts API credentials first.")
        raise typer.Exit(2)
    if not provider.rights_confirmed:
        console.print("[yellow]No data fetched or stored.[/yellow] Written rights for local history and public-playlist research are required.")
        raise typer.Exit(2)
    try:
        with SessionFactory() as session:
            run = sync_soundcharts_charts(
                session,
                provider,
                chart_slugs=[slug.strip() for slug in charts.split(",") if slug.strip()],
                max_requests=max_requests,
                limit=limit,
            )
            session.commit()
    except SoundchartsRightsNotConfirmed as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(2) from exc
    except SoundchartsNotConfigured as exc:
        console.print(f"[yellow]No Soundcharts data fetched:[/yellow] {exc}")
        raise typer.Exit(2) from exc
    except (ValueError, SoundchartsAPIError) as exc:
        code = getattr(exc, "code", type(exc).__name__)
        status_code = getattr(exc, "status_code", "")
        console.print(f"[red]Soundcharts sync failed:[/red] {status_code} {code}")
        raise typer.Exit(1) from exc
    except SQLAlchemyError as exc:
        console.print(f"[red]Soundcharts sync was not committed:[/red] database error {type(exc).__name__}")
        raise typer.Exit(1) from exc
    finally:
        provider.close()
    color = "green" if run.status == "SUCCEEDED" else "yellow" if run.status == "PARTIAL" else "red"
    console.print(f"Soundcharts sync: [{color}]{run.status}[/{color}]")
    console.print(f"Requests: {run.request_count}; observations: {run.observations_written}; markets: {', '.join(run.markets_requested) or 'none'}")
    console.print(f"Quota: {run.rate_limit_status}")
    if run.error_code:
        console.print(f"Provider report: {run.error_code}")


@world_app.command("chartmetric-check")
def chartmetric_check() -> None:
    """Verify Chartmetric API access only after its reuse license is configured."""
    provider = ChartmetricProvider.from_environment()
    if provider is None or not provider.credentials_configured:
        console.print("[yellow]Chartmetric API credentials are not configured.[/yellow] A free dashboard account is not API access.")
        console.print("Configure CHARTMETRIC_REFRESH_TOKEN only after receiving API access and written rights for this workflow.")
        raise typer.Exit(2)
    if not provider.rights_confirmed:
        console.print("[yellow]No API request made.[/yellow] Obtain written rights for snapshots, derived scores, and playlist curation before verifying data access.")
        raise typer.Exit(2)
    try:
        result = provider.verify()
    except ChartmetricAPIError as exc:
        console.print(f"[red]Chartmetric API check failed:[/red] {exc.status_code} {exc.code}. Check credentials and plan access.")
        raise typer.Exit(1) from exc
    except ChartmetricFreeTrialRequired as exc:
        console.print(f"[yellow]No Chartmetric API request made:[/yellow] {exc}")
        raise typer.Exit(2) from exc
    except ChartmetricRightsNotConfirmed as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(2) from exc
    finally:
        provider.close()
    console.print(f"Chartmetric API: [green]{result.state}[/green]")
    console.print(f"Quota/rate report: {result.rate_limit_status}")


@world_app.command("chartmetric-sync")
def chartmetric_sync(
    markets: str | None = typer.Option(None, help="Comma-separated ISO country codes (defaults to CHARTMETRIC_MARKETS)."),
    limit: int = typer.Option(100, min=1, max=100),
    max_requests: int = typer.Option(20, min=1, max=100, help="Hard API request budget for one sync."),
) -> None:
    """Capture licensed Chartmetric growth and chart observations, then update source-backed scores."""
    provider = ChartmetricProvider.from_environment()
    if provider is None or not provider.credentials_configured:
        console.print("[yellow]No data fetched.[/yellow] Configure Chartmetric API access first; the free dashboard is not an API token.")
        raise typer.Exit(2)
    if not provider.rights_confirmed:
        console.print("[yellow]No data fetched or stored.[/yellow] Written rights for stored observations, derived scores, and playlist curation are required.")
        raise typer.Exit(2)
    env = dotenv_values(PROJECT_ROOT / ".env")
    market_list = markets or os.environ.get("CHARTMETRIC_MARKETS") or env.get("CHARTMETRIC_MARKETS") or "CL,MX,AR,CO,ES,US,BR"
    try:
        with SessionFactory() as session:
            run = run_chartmetric_sync(
                session,
                provider,
                market_codes=[code.strip() for code in str(market_list).split(",") if code.strip()],
                limit=limit,
                max_requests=max_requests,
            )
            scores = recalculate_trend_scores(session)
            session.commit()
    except ChartmetricRightsNotConfirmed as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(2) from exc
    except ChartmetricFreeTrialRequired as exc:
        console.print(f"[yellow]No Chartmetric data fetched:[/yellow] {exc}")
        raise typer.Exit(2) from exc
    except SQLAlchemyError as exc:
        console.print(f"[red]Chartmetric sync was not committed:[/red] database error {type(exc).__name__}")
        raise typer.Exit(1) from exc
    finally:
        provider.close()
    color = "green" if run.status == "SUCCEEDED" else "yellow" if run.status in {"PARTIAL", "SKIPPED"} else "red"
    console.print(f"Chartmetric sync: [{color}]{run.status}[/{color}]")
    console.print(f"Requests: {run.request_count}; observations: {run.observations_written}; markets: {', '.join(run.markets_requested)}")
    console.print(f"Source-backed score rows: {scores['score_rows_written']}; scored tracks: {scores['scores_created']}")
    if run.error_code:
        console.print(f"Provider/plan report: {run.error_code}")


@world_app.command("scores")
def calculate_scores_command() -> None:
    """Explain which trend scores are supported by the connected source."""
    console.print("Trend/Breakout/NEXT scores are derived only from licensed Chartmetric observations with adequate history and remain low-confidence when source coverage is not reported. Last.fm and Soundcharts chart ranks/playcounts stay labeled as source observations; none predicts Spotify placement.")


@world_app.command("playlist-drafts")
def playlist_drafts(
    playlist_slug: str | None = typer.Option(None, help="Build a draft for one configured playlist; omit to evaluate all ten."),
    limit: int = typer.Option(25, min=1, max=100, help="Maximum proposed additions per playlist, before turnover caps."),
) -> None:
    """Generate local, review-only playlist drafts without writing to Spotify."""
    try:
        with SessionFactory() as session:
            result = generate_public_playlist_drafts(
                session,
                merge_provider_sync_statuses(session, ProviderRegistry().statuses()),
                playlist_slug=playlist_slug,
                limit=limit,
            )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    except SQLAlchemyError as exc:
        console.print(f"[red]Playlist draft generation failed at the database layer:[/red] {type(exc).__name__}")
        raise typer.Exit(1) from exc
    console.print_json(json.dumps(result, ensure_ascii=False))


@world_app.command("playlist-launch")
def playlist_launch(
    slug: str | None = typer.Option(None, help="Evaluate one of the 100 expansion concepts; omit for all."),
    apply: bool = typer.Option(False, "--apply", help="Create only playlists that pass the content and evidence gates."),
) -> None:
    """Generate, validate, and optionally publish evidence-backed playlist seeds."""
    try:
        with SessionFactory() as session:
            plans = build_blueprint_launch_plans(
                session,
                merge_provider_sync_statuses(session, ProviderRegistry().statuses()),
                existing_configs=load_playlists(),
                slug=slug,
            )
            report = {
                "generated_at": now_utc().isoformat(),
                "concept_count": len(plans),
                "ready_count": sum(plan["status"] == "READY_TO_LAUNCH" for plan in plans),
                "max_new_playlists_per_apply": MAX_NEW_PLAYLISTS_PER_RUN,
                "minimum_seed_tracks": 30,
                "minimum_unique_artists": 20,
                "items": [plan_public_view(plan) for plan in plans],
            }
            JsonStore(DATA_DIR / "playlist_launch_plan.json").write(report)
    except (ValueError, SQLAlchemyError) as exc:
        console.print(f"[red]Playlist launch plan failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(f"Launch plan: {report['ready_count']}/{report['concept_count']} concepts ready; review data/playlist_launch_plan.json")
    for plan in plans:
        blockers = ", ".join(plan["blockers"]) or "none"
        console.print(f"{plan['slug']}: {plan['status']} · {plan['candidate_count']} tracks · {plan['unique_artist_count']} artists · {blockers}")
    if apply:
        outcomes = publish_ready_blueprints(plans)
        if outcomes:
            console.print_json(json.dumps(outcomes, ensure_ascii=False))
            deferred = max(0, report["ready_count"] - len(outcomes))
            if deferred:
                console.print(f"{deferred} additional concepts remain queued for later review batches.")
        else:
            console.print("No Spotify playlist was created: no concept passed the creation gate.")
