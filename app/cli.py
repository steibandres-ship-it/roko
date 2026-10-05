from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .auth import OAuthError, OAuthManager, TokenStore
from .config import DATA_DIR, PLAYLISTS_DIR, PROJECT_ROOT, ensure_project_dirs, load_settings
from .models import PlaylistConfig, TrackSpec, extract_track_id
from .neuromental import parse_annotation, plan_sequence, profile_coverage
from .playlists import (
    PlaylistConfigError,
    create_one,
    get_playlist_config,
    load_playlists,
    prepare_cover,
    read_backup,
    recover_matches,
    resolve_cover_path,
    store_recovered_playlist,
)
from .spotify_client import SpotifyAPIError, SpotifyClient
from .storage import JsonStore, PlaylistRegistry
from .sync import resolve_all, resolve_playlist, restore_backup, sync_playlist
from .trends import load_trend_batch, rotate_from_charts
from .tracks import remove_config_duplicates
from .world_music.commands import world_app


for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass

console = Console()
app = typer.Typer(no_args_is_help=True, help="WORLD MUSIC OS: music data, discovery, playlists, and authorized publishing.")
playlists_app = typer.Typer(no_args_is_help=True, help="Create, inspect, and recover playlist registrations.")
tracks_app = typer.Typer(no_args_is_help=True, help="Resolve configured songs against the Spotify catalog.")
neuromental_app = typer.Typer(no_args_is_help=True, help="Inspect non-clinical listening-context curation profiles.")
covers_app = typer.Typer(no_args_is_help=True, help="Upload custom images for registered Spotify playlists.")
trends_app = typer.Typer(no_args_is_help=True, help="Review external music charts and rotate one track per playlist safely.")
app.add_typer(playlists_app, name="playlists")
app.add_typer(tracks_app, name="tracks")
app.add_typer(neuromental_app, name="neuromental")
app.add_typer(covers_app, name="covers")
app.add_typer(trends_app, name="trends")
app.add_typer(world_app, name="world")


def _settings(*, require_client_id: bool = True):
    try:
        settings = load_settings(require_client_id=require_client_id)
        ensure_project_dirs()
        return settings
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc


def _client() -> SpotifyClient:
    settings = _settings()
    return SpotifyClient(settings, OAuthManager(settings))


def _account(spotify: SpotifyClient) -> dict:
    profile = spotify.get_me()
    name = profile.get("display_name") or profile.get("id") or "Spotify user"
    console.print(f"Account: [bold]{name}[/bold]")
    return profile


def _select_playlists(slug: str | None, all_playlists: bool) -> list[PlaylistConfig]:
    configs = load_playlists()
    if all_playlists and slug:
        raise ValueError("choose one playlist slug or --all, not both")
    if all_playlists:
        return configs
    if not slug:
        raise ValueError("provide a playlist slug or use --all")
    return [get_playlist_config(slug)]


@app.command()
def auth(
    with_covers: bool = typer.Option(False, "--with-covers", help="Also authorize custom playlist cover uploads."),
) -> None:
    """Authorize this local app with Spotify using Authorization Code + PKCE."""
    settings = _settings()
    manager = OAuthManager(settings, TokenStore(settings.client_id), timeout=settings.request_timeout)
    tokens = manager.authorize(with_covers=with_covers)
    with SpotifyClient(settings, manager) as spotify:
        profile = _account(spotify)
    granted = set(str(tokens.get("scope", "")).split())
    if with_covers and "ugc-image-upload" not in granted:
        console.print("[yellow]Spotify did not grant the optional ugc-image-upload scope; cover uploads will require another authorization.[/yellow]")
    console.print("Authorization verified successfully. Access tokens are not displayed.")


@app.command()
def account() -> None:
    """Show the Spotify account connected to this project."""
    with _client() as spotify:
        _account(spotify)


@playlists_app.command("list")
def playlists_list() -> None:
    """List local playlist configurations and their registered Spotify IDs."""
    configs = load_playlists()
    registry = PlaylistRegistry().all()
    table = Table(title="Spotify Playlist Network")
    table.add_column("Slug")
    table.add_column("Playlist")
    table.add_column("Target", justify="right")
    table.add_column("Spotify registration")
    for config in configs:
        record = registry.get(config.slug, {})
        table.add_row(config.slug, config.name, str(config.target_tracks), record.get("spotify_playlist_id", "not registered"))
    console.print(table)


@playlists_app.command("create")
def playlists_create(
    slug: Optional[str] = typer.Argument(None),
    all_playlists: bool = typer.Option(False, "--all", help="Create every playlist missing from the registry."),
) -> None:
    """Create registered playlists once, without duplicating existing IDs."""
    configs = _select_playlists(slug, all_playlists)
    settings = _settings()
    manager = OAuthManager(settings)
    registry = PlaylistRegistry()
    with SpotifyClient(settings, manager) as spotify:
        profile = _account(spotify)
        owner_id = str(profile.get("id") or "")
        tokens = manager.token_store.load()
        known_playlists = spotify.get_playlists() if any(not registry.get(config.slug) for config in configs) else []
        for config in configs:
            url, created = create_one(
                spotify,
                config,
                registry,
                owner_id,
                token_data=tokens,
                known_playlists=known_playlists,
            )
            state = "Created" if created else "Already registered"
            console.print(f"{state}: [bold]{config.name}[/bold] — {url}")
            if created:
                known_playlists.append({"id": url.rsplit("/", 1)[-1], "name": config.name, "owner": {"id": owner_id}})


@playlists_app.command("recover")
def playlists_recover(
    slug: str = typer.Argument(...),
) -> None:
    """Find exact-name owned playlists for one slug and ask before recording the ID."""
    config = get_playlist_config(slug)
    registry = PlaylistRegistry()
    if registry.get(slug):
        console.print(f"{slug} is already registered; no change made.")
        return
    with _client() as spotify:
        profile = spotify.get_me()
        owner_id = str(profile.get("id") or "")
        matches = recover_matches(spotify, [config], owner_id=owner_id).get(slug, [])
        if not matches:
            console.print(f"No playlist owned by this account exactly matches {config.name!r}.")
            return
        table = Table(title=f"Recovery matches for {config.name}")
        table.add_column("#")
        table.add_column("Spotify playlist ID")
        table.add_column("URL")
        for index, item in enumerate(matches, start=1):
            table.add_row(str(index), str(item.get("id")), (item.get("external_urls") or {}).get("spotify", ""))
        console.print(table)
        raw = console.input("Type the match number to register, or press Enter to cancel: ").strip()
        if not raw.isdigit() or not 1 <= int(raw) <= len(matches):
            console.print("Cancelled; registry unchanged.")
            return
        selected = matches[int(raw) - 1]
        if not typer.confirm(f"Register Spotify playlist {selected.get('id')} for {slug}?", default=False):
            console.print("Cancelled; registry unchanged.")
            return
        store_recovered_playlist(config, selected, registry, owner_id)
        console.print(f"Recovered {slug}: {(selected.get('external_urls') or {}).get('spotify')}")


@app.command()
def sync(
    slug: Optional[str] = typer.Argument(None),
    all_playlists: bool = typer.Option(False, "--all", help="Synchronize all configured playlists."),
    mode: str = typer.Option("append", "--mode", case_sensitive=False, help="append or exact"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without modifying Spotify."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation for destructive exact sync changes."),
    remove_locked: bool = typer.Option(False, "--remove-locked", help="Allow exact sync to remove previously locked songs omitted from JSON."),
) -> None:
    """Synchronize one playlist or the whole network."""
    normalized_mode = mode.lower()
    if normalized_mode not in {"append", "exact"}:
        raise typer.BadParameter("mode must be append or exact")
    if remove_locked and normalized_mode != "exact":
        raise typer.BadParameter("--remove-locked is only available with --mode exact")
    configs = _select_playlists(slug, all_playlists)
    settings = _settings()
    registry = PlaylistRegistry()
    manager = OAuthManager(settings)
    token_data = manager.token_store.load()
    with SpotifyClient(settings, manager) as spotify:
        profile = _account(spotify)
        blocked: list[str] = []
        for config in configs:
            result = sync_playlist(
                config,
                spotify,
                registry=registry,
                mode=normalized_mode,  # type: ignore[arg-type]
                dry_run=dry_run,
                yes=yes,
                remove_locked=remove_locked,
                owner_id=str(profile.get("id") or ""),
                token_data=token_data,
            )
            if result.get("blocked_unresolved"):
                blocked.append(config.slug)
        if blocked:
            console.print(f"[red]Exact sync paused for unresolved playlists: {', '.join(blocked)}[/red]")
            raise typer.Exit(1)


@covers_app.command("upload")
def covers_upload(
    slug: Optional[str] = typer.Argument(None),
    all_playlists: bool = typer.Option(False, "--all", help="Upload covers for every configured playlist."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Check local images without uploading."),
) -> None:
    """Upload custom playlist covers without reading or changing playlist tracks."""
    configs = _select_playlists(slug, all_playlists)
    registry = PlaylistRegistry()
    pending: list[tuple[PlaylistConfig, str, bytes, str]] = []
    for config in configs:
        record = registry.get(config.slug) or {}
        playlist_id = str(record.get("spotify_playlist_id") or "")
        if not playlist_id:
            raise typer.BadParameter(f"{config.slug} is not registered; run playlists create or recover first")
        cover_path = resolve_cover_path(config.cover)
        if not cover_path or not cover_path.is_file():
            raise typer.BadParameter(f"{config.slug} has no readable custom cover configured")
        payload = prepare_cover(cover_path)
        digest = hashlib.sha256(payload).hexdigest()
        if record.get("cover_sha256") == digest:
            console.print(f"{config.slug}: cover already matches the last uploaded image")
            continue
        pending.append((config, playlist_id, payload, digest))
        console.print(f"{config.slug}: ready ({len(payload)} JPEG bytes)")

    if not pending:
        console.print("No cover uploads are pending.")
        return
    if dry_run:
        console.print("Dry run: no Spotify changes made.")
        return

    settings = _settings()
    tokens = TokenStore(settings.client_id).load() or {}
    if "ugc-image-upload" not in set(str(tokens.get("scope", "")).split()):
        console.print("[red]Spotify cover permission is missing. Run `python -m app auth --with-covers` and grant ugc-image-upload.[/red]")
        raise typer.Exit(2)

    with SpotifyClient(settings, OAuthManager(settings)) as spotify:
        owner_id = str(spotify.get_me().get("id") or "")
        for config, playlist_id, payload, digest in pending:
            record = registry.get(config.slug) or {}
            details = spotify.get_playlist(playlist_id)
            actual_owner = str((details.get("owner") or {}).get("id") or "")
            if not owner_id or actual_owner != owner_id or str(record.get("owner_id") or "") != owner_id:
                raise ValueError(f"Ownership check failed for {config.slug}; cover upload was blocked.")
            spotify.upload_cover(playlist_id, payload)
            registry.update(config.slug, {"cover_sha256": digest})
            console.print(f"[green]{config.slug}: cover upload accepted by Spotify.[/green]")


@trends_app.command("rotate")
def trends_rotate(
    candidates: Path = typer.Option(DATA_DIR / "daily_trends.json", "--candidates", help="External chart candidates in JSON format."),
    apply: bool = typer.Option(False, "--apply", help="Apply the reviewed daily rotation to Spotify, with exact-sync backups."),
    max_changes: int = typer.Option(10, "--max-changes", min=0, max=10, help="Maximum one-track rotations across the network today."),
) -> None:
    """Rotate eligible tracks based on externally sourced charts; preview unless --apply is supplied."""
    try:
        batch = load_trend_batch(candidates)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    settings = _settings()
    manager = OAuthManager(settings)
    with SpotifyClient(settings, manager) as spotify:
        profile = spotify.get_me()
        result = rotate_from_charts(
            batch,
            spotify,
            owner_id=str(profile.get("id") or ""),
            apply=apply,
            max_changes=max_changes,
        )
    label = "Applied" if apply else "Previewed"
    console.print(
        f"{label}: {result['applied'] if apply else result['previewed']} rotations; {result['already_present']} already present; "
        f"{result['unmatched']} catalog mismatches; {result['skipped']} skipped."
    )


@tracks_app.command("resolve")
def tracks_resolve(
    slug: Optional[str] = typer.Argument(None),
    all_playlists: bool = typer.Option(False, "--all", help="Resolve every configured playlist."),
) -> None:
    """Resolve tracks by Spotify ID/URL or a conservative catalog search."""
    configs = _select_playlists(slug, all_playlists)
    with _client() as spotify:
        if all_playlists:
            resolve_all(configs, spotify)
        else:
            resolve_playlist(configs[0], spotify)


@app.command()
def status() -> None:
    """Show configuration, local registration, and (when authorized) account status."""
    configs = load_playlists()
    registry = PlaylistRegistry().all()
    console.print("[bold]SPOTIFY PLAYLIST NETWORK[/bold]")
    console.print(f"Configured playlists: {len(configs)}")
    console.print(f"Registered playlists: {sum(1 for config in configs if registry.get(config.slug, {}).get('spotify_playlist_id'))}")
    unresolved = JsonStore(DATA_DIR / "unresolved_tracks.json").read([])
    console.print(f"Saved unresolved tracks: {len(unresolved)}")
    settings = load_settings(require_client_id=False)
    if settings.client_id == "not-configured":
        console.print("Spotify account: not connected (set SPOTIFY_CLIENT_ID in .env, then run auth)")
        return
    token_data = TokenStore(settings.client_id).load()
    if not token_data:
        console.print("Spotify account: not connected (run python -m app auth)")
        return
    with SpotifyClient(settings, OAuthManager(settings)) as spotify:
        profile = spotify.get_me()
        console.print(f"Spotify account: {profile.get('display_name') or profile.get('id')}")
        table = Table(title="Playlists")
        table.add_column("Playlist")
        table.add_column("Tracks", justify="right")
        for config in configs:
            record = registry.get(config.slug, {})
            if record.get("spotify_playlist_id"):
                info = spotify.get_playlist(str(record["spotify_playlist_id"]))
                count = str(((info.get("items") or {}).get("total")) or ((info.get("tracks") or {}).get("total")) or 0)
                table.add_row(config.name, count)
            else:
                table.add_row(config.name, "not registered")
        console.print(table)


@app.command()
def validate() -> None:
    """Validate all local JSON playlist configurations without contacting Spotify."""
    configs = load_playlists()
    total = 0
    duplicate_total = 0
    for config in configs:
        unique, duplicates = remove_config_duplicates(config.tracks)
        total += len(config.tracks)
        duplicate_total += duplicates
        console.print(f"✓ {config.slug}: {len(config.tracks)} configured tracks, {duplicates} duplicate entries")
    console.print(f"Valid: {len(configs)} playlists, {total} configured entries, {duplicate_total} duplicate entries.")


@neuromental_app.command("report")
def neuromental_report(
    slug: Optional[str] = typer.Argument(None),
    all_playlists: bool = typer.Option(False, "--all", help="Show every playlist profile (the default)."),
) -> None:
    """Show editorial intent, sequencing, and how many tracks have curator ratings."""
    if slug and all_playlists:
        raise typer.BadParameter("choose one playlist slug or --all, not both")
    configs = [get_playlist_config(slug)] if slug else load_playlists()
    table = Table(title="Neuromental listening-context profiles")
    table.add_column("Playlist")
    table.add_column("Intents")
    table.add_column("Energy / Mood / Focus / Social")
    table.add_column("Sequence arc")
    table.add_column("Rated tracks", justify="right")
    table.add_column("Mean fit", justify="right")
    for config in configs:
        profile = config.neuromental
        if profile is None:
            table.add_row(config.name, "not configured", "—", "—", "0/0", "—")
            continue
        scored, total, average = profile_coverage(config.tracks, profile)
        targets = "/".join(
            str(value) if value is not None else "—"
            for value in (profile.energy_target, profile.valence_target, profile.focus_target, profile.social_energy_target)
        )
        table.add_row(
            config.name,
            ", ".join(profile.intents) or "—",
            targets,
            " → ".join(profile.sequence_arc) or "—",
            f"{scored}/{total}",
            f"{average}/100" if average is not None else "—",
        )
    console.print(table)
    console.print("Ratings are editorial listening-context cues (1–5), not measurements or clinical advice. Missing cues are left unscored.")


@neuromental_app.command("rate")
def neuromental_rate(
    slug: str = typer.Argument(...),
    start: int = typer.Option(1, "--start", min=1, help="Track number to resume from."),
) -> None:
    """Guide a human curator through explicit track ratings; nothing is inferred."""
    config = get_playlist_config(slug)
    if config.neuromental is None:
        raise typer.BadParameter(f"{slug} has no neuromental listening-context profile")
    path = PLAYLISTS_DIR / f"{slug}.json"
    raw = JsonStore(path).read(None)
    rows = raw.get("tracks", []) if isinstance(raw, dict) else []
    if start > len(rows):
        raise typer.BadParameter(f"--start must be between 1 and {len(rows)}")
    console.print(f"[bold]{config.name}[/bold] — escucha cada pista antes de puntuarla.")
    console.print(f"Intenciones: {', '.join(config.neuromental.intents) or '—'}")
    console.print(f"Arco sugerido: {' → '.join(config.neuromental.sequence_arc) or '—'}")
    console.print("Formato: energía,ánimo,foco,social;rol;intención|intención;nota")
    console.print("Usa valores 1–5; '-' borra un campo, '=' lo conserva. Enter omite; q termina.")
    console.print("Intenciones: activation, uplift, focus, social-connection, reflection, wind-down, discovery, self-expression, curiosity.")
    changed = 0
    for index in range(start - 1, len(rows)):
        entry = rows[index]
        spec = TrackSpec.model_validate(entry)
        direct = spec.spotify_uri or spec.spotify_url
        if not direct:
            console.print(f"{index + 1}/{len(rows)}: sin URI de Spotify; se omite.")
            continue
        track_id = extract_track_id(direct)
        url = f"https://open.spotify.com/track/{track_id}"
        console.print(f"\nPista {index + 1}/{len(rows)} — prioridad {spec.priority}")
        console.print(f"[link={url}]Abrir esta pista en Spotify[/link]")
        if spec.neuromental:
            current = spec.neuromental
            console.print(
                "Actual: "
                f"{current.energy or '—'},{current.valence or '—'},{current.focus or '—'},{current.social_energy or '—'};"
                f"{current.sequence_role or '—'};{'|'.join(current.intent_tags) or '—'}"
            )
        answer = console.input("Tu valoración (Enter omite, q termina): ").strip()
        if answer.casefold() == "q":
            break
        if not answer or answer.casefold() in {"s", "skip"}:
            continue
        try:
            annotation = parse_annotation(answer, spec.neuromental)
        except Exception as exc:
            console.print(f"[yellow]Valoración no guardada:[/yellow] {exc}")
            continue
        entry["neuromental"] = annotation.model_dump(exclude_none=True)
        JsonStore(path).write(raw)
        changed += 1
    current_config = get_playlist_config(slug)
    scored, total, average = profile_coverage(current_config.tracks, current_config.neuromental)
    console.print(f"Guardadas {changed} valoraciones. Cobertura: {scored}/{total}; promedio de ajuste: {average if average is not None else '—'}.")


@neuromental_app.command("sequence")
def neuromental_sequence(
    slug: str = typer.Argument(...),
    apply: bool = typer.Option(False, "--apply", help="Apply the reviewed role order to Spotify after exact-sync confirmation."),
) -> None:
    """Preview or apply an order based only on human-assigned sequence roles."""
    config = get_playlist_config(slug)
    if config.neuromental is None:
        raise typer.BadParameter(f"{slug} has no neuromental listening-context profile")
    planned, role_tagged, positions_changed = plan_sequence(config.tracks, config.neuromental)
    console.print(f"{config.name}: {role_tagged} tracks have explicit sequence roles.")
    console.print(f"Suggested arc: {' → '.join(config.neuromental.sequence_arc) or '—'}")
    console.print(f"Positions that would change: {positions_changed}")
    if not positions_changed:
        console.print("No order change suggested. Add human sequence roles with `python -m app neuromental rate` first.")
        return
    if not apply:
        console.print("Preview only. Run with --apply to sync this order to Spotify; exact sync will show a diff and ask before changing it.")
        return

    settings = _settings()
    registry = PlaylistRegistry()
    manager = OAuthManager(settings)
    path = PLAYLISTS_DIR / f"{slug}.json"
    raw = JsonStore(path).read(None)
    original_rows = raw.get("tracks", [])
    index_by_object = {id(track): index for index, track in enumerate(config.tracks)}
    ordered_rows = [original_rows[index_by_object[id(track)]] for track in planned]
    ordered_config = config.model_copy(update={"tracks": planned})
    with SpotifyClient(settings, manager) as spotify:
        owner_id = str(spotify.get_me().get("id") or "")
        result = sync_playlist(
            ordered_config,
            spotify,
            registry=registry,
            mode="exact",
            owner_id=owner_id,
            token_data=manager.token_store.load(),
        )
    if result.get("cancelled") or result.get("blocked_unresolved"):
        return
    raw["tracks"] = ordered_rows
    JsonStore(path).write(raw)
    console.print("Human-role sequence saved locally and synchronized.")


@app.command()
def restore(
    backup_path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show restore changes without modifying Spotify."),
) -> None:
    """Preview and confirm restoring a local playlist backup."""
    backup = read_backup(backup_path)
    settings = _settings()
    registry = PlaylistRegistry()
    with SpotifyClient(settings, OAuthManager(settings)) as spotify:
        profile = spotify.get_me()
        restore_backup(
            backup,
            spotify,
            registry=registry,
            dry_run=dry_run,
            owner_id=str(profile.get("id") or ""),
        )


def main() -> None:
    ensure_project_dirs()
    try:
        app()
    except (OAuthError, SpotifyAPIError, PlaylistConfigError, ValueError, OSError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc


if __name__ == "__main__":
    main()
