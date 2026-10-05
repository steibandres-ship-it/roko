from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any, Literal

from rich.console import Console
from rich.table import Table

from .config import DATA_DIR
from .models import DiffSummary, PlaylistConfig, ResolvedTrack
from .playlists import create_backup, resolve_cover_path, prepare_cover
from .storage import JsonStore, PlaylistRegistry, append_history, append_unresolved, utc_now
from .tracks import TrackResolver, duplicate_key, remove_config_duplicates


console = Console()


def item_uri(entry: dict[str, Any]) -> str | None:
    item = entry.get("item") or entry.get("track") or {}
    uri = item.get("uri") if isinstance(item, dict) else None
    return str(uri) if uri else None


def _lis_length(values: list[int]) -> int:
    import bisect

    tails: list[int] = []
    for value in values:
        index = bisect.bisect_left(tails, value)
        if index == len(tails):
            tails.append(value)
        else:
            tails[index] = value
    return len(tails)


def calculate_diff(current: list[str], desired: list[str]) -> DiffSummary:
    current_counts = Counter(current)
    desired_counts = Counter(desired)
    additions: list[str] = []
    removals: list[str] = []
    for uri, count in desired_counts.items():
        additions.extend([uri] * max(0, count - current_counts.get(uri, 0)))
    for uri, count in current_counts.items():
        removals.extend([uri] * max(0, count - desired_counts.get(uri, 0)))

    remaining_desired = desired_counts.copy()
    current_common = []
    for uri in current:
        if remaining_desired[uri] > 0:
            current_common.append(uri)
            remaining_desired[uri] -= 1
    remaining_current = current_counts.copy()
    desired_common = []
    for uri in desired:
        if remaining_current[uri] > 0:
            desired_common.append(uri)
            remaining_current[uri] -= 1
    current_positions = {uri: index for index, uri in enumerate(current_common)}
    sequence = [current_positions[uri] for uri in desired_common if uri in current_positions]
    moves = max(0, len(sequence) - _lis_length(sequence))
    common = sum(min(count, desired_counts.get(uri, 0)) for uri, count in current_counts.items())
    return DiffSummary(additions=additions, removals=removals, moves=moves, unchanged=max(0, common - moves))


def _resolved_specs(playlist: PlaylistConfig, spotify: Any) -> tuple[list[ResolvedTrack], list[dict[str, Any]], int, int, set[str]]:
    unique_specs, config_duplicates = remove_config_duplicates(playlist.tracks)
    locked_keys = {duplicate_key(spec) for spec in playlist.tracks if spec.locked}
    resolver = TrackResolver(spotify)
    resolved: list[ResolvedTrack] = []
    unresolved: list[dict[str, Any]] = []
    seen_uris: set[str] = set()
    resolved_duplicates = 0
    locked_uris: set[str] = set()
    for spec in unique_specs:
        track, candidates, reason = resolver.resolve(spec)
        if not track:
            unresolved.append(
                {
                    "playlist": playlist.slug,
                    "artist": spec.artist,
                    "title": spec.title,
                    "spotify_uri": spec.spotify_uri,
                    "reason": reason,
                    "candidates": candidates,
                    "updated_at": utc_now(),
                }
            )
            continue
        if duplicate_key(spec) in locked_keys:
            locked_uris.add(track.uri)
        if track.uri in seen_uris:
            resolved_duplicates += 1
            continue
        seen_uris.add(track.uri)
        resolved.append(track)
    return resolved, unresolved, config_duplicates + resolved_duplicates, len(playlist.tracks), locked_uris


def _print_resolution(playlist: PlaylistConfig, resolved: list[ResolvedTrack], unresolved: list[dict[str, Any]], duplicates: int, configured: int) -> None:
    console.print(f"[bold]{playlist.name}[/bold]")
    console.print(f"Tracks configured: {configured}")
    console.print(f"Tracks resolved: {len(resolved)}")
    console.print(f"Duplicates eliminated: {duplicates}")
    console.print(f"Not found / ambiguous: {len(unresolved)}")
    for entry in unresolved:
        console.print(f"[yellow]Could not safely resolve: {entry.get('artist') or 'Unknown artist'} — {entry.get('title') or entry.get('spotify_uri') or 'Unknown track'}[/yellow]")
        console.print(f"  Reason: {entry.get('reason')}")
        for candidate in entry.get("candidates", []):
            artists = ", ".join(candidate.get("artists", []))
            score = candidate.get("score")
            score_text = f" ({score:.3f})" if isinstance(score, (float, int)) else ""
            console.print(f"  • {candidate.get('name')} — {artists}{score_text} | {candidate.get('spotify_url') or candidate.get('uri')}")


def resolve_playlist(playlist: PlaylistConfig, spotify: Any, *, unresolved_path: Path | None = None) -> list[ResolvedTrack]:
    resolved, unresolved, duplicates, configured, _ = _resolved_specs(playlist, spotify)
    _print_resolution(playlist, resolved, unresolved, duplicates, configured)
    current = JsonStore(unresolved_path or DATA_DIR / "unresolved_tracks.json").read([])
    by_playlist = [item for item in current if item.get("playlist") != playlist.slug]
    append_unresolved(by_playlist + unresolved, unresolved_path)
    return resolved


def resolve_all(playlists: list[PlaylistConfig], spotify: Any, *, unresolved_path: Path | None = None) -> list[ResolvedTrack]:
    unresolved: list[dict[str, Any]] = []
    all_resolved: list[ResolvedTrack] = []
    for playlist in playlists:
        resolved, missing, duplicates, configured, _ = _resolved_specs(playlist, spotify)
        _print_resolution(playlist, resolved, missing, duplicates, configured)
        all_resolved.extend(resolved)
        unresolved.extend(missing)
    existing = JsonStore(unresolved_path or DATA_DIR / "unresolved_tracks.json").read([])
    selected = {playlist.slug for playlist in playlists}
    kept = [entry for entry in existing if entry.get("playlist") not in selected]
    append_unresolved(kept + unresolved, unresolved_path)
    return all_resolved


def _cover_payload(playlist: PlaylistConfig, token_data: dict[str, Any] | None) -> bytes | None:
    path = resolve_cover_path(playlist.cover)
    if not path or not path.is_file():
        return None
    if "ugc-image-upload" not in set((token_data or {}).get("scope", "").split()):
        raise ValueError("This playlist has a cover, but its Spotify session lacks ugc-image-upload. Run python -m app auth --with-covers.")
    return prepare_cover(path)


def sync_playlist(
    playlist: PlaylistConfig,
    spotify: Any,
    *,
    registry: PlaylistRegistry | None = None,
    mode: Literal["append", "exact"] = "append",
    dry_run: bool = False,
    yes: bool = False,
    remove_locked: bool = False,
    owner_id: str | None = None,
    token_data: dict[str, Any] | None = None,
    backup_root: Path | None = None,
    unresolved_path: Path | None = None,
    history_path: Path | None = None,
) -> dict[str, Any]:
    registry = registry or PlaylistRegistry()
    record = registry.get(playlist.slug)
    if not record or not record.get("spotify_playlist_id"):
        raise ValueError("Playlist is not registered. Run playlist creation or recovery.")
    playlist_id = str(record["spotify_playlist_id"])
    details = spotify.get_playlist(playlist_id)
    if owner_id and str((details.get("owner") or {}).get("id") or record.get("owner_id") or "") != owner_id:
        raise ValueError("Registered playlist does not belong to the connected Spotify account.")
    items = spotify.get_playlist_items(playlist_id)
    current_uris = [uri for entry in items if (uri := item_uri(entry))]
    resolved, unresolved, duplicates, configured_unique, configured_locks = _resolved_specs(playlist, spotify)
    _print_resolution(playlist, resolved, unresolved, duplicates, configured_unique)
    existing_unresolved = JsonStore(unresolved_path or DATA_DIR / "unresolved_tracks.json").read([])
    kept_unresolved = [entry for entry in existing_unresolved if entry.get("playlist") != playlist.slug]
    append_unresolved(kept_unresolved + unresolved, unresolved_path)

    desired = [track.uri for track in resolved]
    old_locks = set(record.get("locked_uris", [])) & set(current_uris)
    if mode == "exact" and not remove_locked:
        preserve = old_locks - set(desired)
        for uri in [entry for entry in current_uris if entry in preserve]:
            index = current_uris.index(uri)
            desired.insert(min(index, len(desired)), uri)
    effective_locks = old_locks | configured_locks
    if mode == "exact" and remove_locked:
        effective_locks &= set(desired)
    target_uris = list(dict.fromkeys(desired))
    if mode == "append":
        to_add = [uri for uri in target_uris if uri not in set(current_uris)]
        diff = DiffSummary(additions=to_add, unchanged=max(0, len(current_uris) - len(to_add)))
    elif mode == "exact":
        diff = calculate_diff(current_uris, target_uris)
        to_add = []
    else:
        raise ValueError("mode must be append or exact")

    metadata_changed = any(
        [
            details.get("name") != playlist.name,
            (details.get("description") or "") != playlist.description,
            details.get("public") is not None and bool(details.get("public")) != playlist.public,
        ]
    )
    cover_path = resolve_cover_path(playlist.cover)
    cover_bytes = _cover_payload(playlist, token_data)
    cover_digest = hashlib.sha256(cover_bytes).hexdigest() if cover_bytes is not None else None
    cover_needs_upload = cover_bytes is not None and record.get("cover_sha256") != cover_digest
    console.print(f"\n[bold]PLAYLIST: {playlist.name}[/bold]")
    console.print(f"+ {len(diff.additions)} songs to add")
    console.print(f"- {len(diff.removals)} songs to remove")
    console.print(f"↕ {diff.moves} position changes")
    console.print(f"= {diff.unchanged} songs unchanged")
    existing_duplicates = sum(max(0, count - 1) for count in Counter(current_uris).values())
    if existing_duplicates:
        action = "exact mode will remove extras" if mode == "exact" else "append mode leaves existing songs unchanged"
        console.print(f"[yellow]Existing Spotify duplicates: {existing_duplicates} ({action}).[/yellow]")
    if metadata_changed:
        console.print("Playlist details will be updated.")
    if cover_path and not cover_path.is_file():
        console.print("Cover skipped: no custom image configured")
    elif not playlist.cover:
        console.print("Cover skipped: no custom image configured")
    blocked_unresolved = mode == "exact" and bool(unresolved)
    if blocked_unresolved:
        console.print("[yellow]Exact sync requires every configured track to resolve first; this playlist will not be changed.[/yellow]")
    if dry_run:
        console.print("Dry run: no Spotify changes made.")
        return {"diff": diff, "dry_run": True, "blocked_unresolved": blocked_unresolved, "resolved": len(resolved), "unresolved": len(unresolved), "duplicates": duplicates}
    if blocked_unresolved:
        return {"diff": diff, "blocked_unresolved": True, "resolved": len(resolved), "unresolved": len(unresolved)}

    destructive = mode == "exact" and bool(diff.removals or diff.moves)
    if destructive and not yes:
        if not console.input("Exact sync changes or removes existing playlist items. Continue? [y/N] ").strip().lower() in {"y", "yes"}:
            console.print("Cancelled; no Spotify changes made.")
            return {"diff": diff, "cancelled": True}
    content_changed = bool(to_add) if mode == "append" else bool(diff.additions or diff.removals or diff.moves)
    exact_suffix_add = (
        mode == "exact"
        and not diff.removals
        and not diff.moves
        and len(target_uris) > len(current_uris)
        and target_uris[: len(current_uris)] == current_uris
    )
    if mode == "exact" and (content_changed or metadata_changed or cover_needs_upload):
        backup_path = create_backup(playlist.slug, playlist_id, details, items, backup_root=backup_root)
        console.print(f"Backup saved: {backup_path}")
    else:
        backup_path = None

    if metadata_changed:
        spotify.update_playlist(playlist_id, name=playlist.name, description=playlist.description, public=playlist.public)
    if mode == "append" and to_add:
        spotify.add_items(playlist_id, to_add)
    elif mode == "exact" and content_changed:
        if exact_suffix_add:
            spotify.add_items(playlist_id, target_uris[len(current_uris) :])
        else:
            first_batch = target_uris[:100]
            spotify.replace_items(playlist_id, first_batch)
            remaining = target_uris[100:]
            if remaining:
                spotify.add_items(playlist_id, remaining)
    if cover_needs_upload and cover_bytes is not None:
        spotify.upload_cover(playlist_id, cover_bytes)

    registry.update(
        playlist.slug,
        {
            "last_synced": utc_now(),
            "locked_uris": sorted(effective_locks),
            **({"cover_sha256": cover_digest} if cover_digest and cover_needs_upload else {}),
        },
    )
    append_history(
        {
            "timestamp": utc_now(),
            "playlist": playlist.slug,
            "playlist_id": playlist_id,
            "mode": mode,
            "configured": configured_unique,
            "resolved": len(resolved),
            "unresolved": len(unresolved),
            "duplicates": duplicates,
            "added": len(diff.additions),
            "removed": len(diff.removals),
            "moves": diff.moves,
            "backup": str(backup_path) if backup_path else None,
        },
        history_path,
    )
    console.print("Sync completed successfully.")
    return {"diff": diff, "resolved": len(resolved), "unresolved": len(unresolved), "duplicates": duplicates, "backup": backup_path}


def restore_backup(
    backup: dict[str, Any],
    spotify: Any,
    *,
    registry: PlaylistRegistry | None = None,
    yes: bool = False,
    dry_run: bool = False,
    owner_id: str | None = None,
    backup_root: Path | None = None,
) -> dict[str, Any]:
    registry = registry or PlaylistRegistry()
    record = registry.get(str(backup["slug"]))
    if not record or str(record.get("spotify_playlist_id")) != str(backup["playlist_id"]):
        raise ValueError("Backup playlist ID does not match the registered Spotify playlist. Restore was blocked.")
    details = spotify.get_playlist(str(backup["playlist_id"]))
    if owner_id and str((details.get("owner") or {}).get("id") or record.get("owner_id") or "") != owner_id:
        raise ValueError("Backup playlist does not belong to the connected Spotify account.")
    current_items = spotify.get_playlist_items(str(backup["playlist_id"]))
    current = [uri for item in current_items if (uri := item_uri(item))]
    desired = [item["spotify_uri"] for item in backup["tracks"]]
    diff = calculate_diff(current, desired)
    metadata = {
        "name": str(backup.get("name") or details.get("name") or ""),
        "description": str(backup.get("description") or ""),
        "public": bool(backup.get("public")),
    }
    metadata_changed = any(details.get(key) != value for key, value in metadata.items())
    content_changed = bool(diff.additions or diff.removals or diff.moves)
    console.print(f"Restore preview for {backup['slug']}: +{len(diff.additions)} -{len(diff.removals)} moves {diff.moves}")
    if dry_run:
        console.print("Dry run: no Spotify changes made.")
        return {"diff": diff, "dry_run": True}
    if not content_changed and not metadata_changed:
        console.print("The playlist already matches this backup; no Spotify changes made.")
        return {"diff": diff, "unchanged": True}
    if not yes:
        if not console.input("Restore this backup to Spotify? [y/N] ").strip().lower() in {"y", "yes"}:
            console.print("Cancelled; no Spotify changes made.")
            return {"diff": diff, "cancelled": True}
    created = create_backup(str(backup["slug"]), str(backup["playlist_id"]), details, current_items, backup_root=backup_root)
    if metadata_changed:
        spotify.update_playlist(str(backup["playlist_id"]), **metadata)
    if content_changed:
        spotify.replace_items(str(backup["playlist_id"]), desired[:100])
        if desired[100:]:
            spotify.add_items(str(backup["playlist_id"]), desired[100:])
    registry.update(str(backup["slug"]), {"last_synced": utc_now()})
    console.print(f"Restore completed. A pre-restore backup was saved at {created}.")
    return {"diff": diff, "backup": created}
