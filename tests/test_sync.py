from pathlib import Path

from app.models import PlaylistConfig
from app.storage import JsonStore, PlaylistRegistry
from app.sync import calculate_diff, sync_playlist
from app.sync import restore_backup


def _track(uri):
    return {"item": {"uri": uri, "name": f"name-{uri}", "artists": [{"name": "Artist"}]}}


class FakeSpotify:
    def __init__(self, items):
        self.items = items
        self.mutations = []

    def get_playlist(self, playlist_id):
        return {
            "id": playlist_id,
            "name": "Test Playlist",
            "description": "Description",
            "public": True,
            "snapshot_id": "snapshot-before",
            "owner": {"id": "owner-1"},
        }

    def get_playlist_items(self, playlist_id):
        return self.items

    def search_tracks(self, *args, **kwargs):
        raise AssertionError("empty config should not search")

    def replace_items(self, playlist_id, uris):
        self.mutations.append(("replace", list(uris)))

    def add_items(self, playlist_id, uris):
        self.mutations.append(("add", list(uris)))

    def remove_items(self, playlist_id, uris, snapshot_id=None):
        self.mutations.append(("remove", list(uris)))

    def update_playlist(self, playlist_id, **kwargs):
        self.mutations.append(("metadata", kwargs))

    def upload_cover(self, *args):
        self.mutations.append(("cover",))


def _config():
    return PlaylistConfig(slug="test-playlist", name="Test Playlist", description="Description", tracks=[])


def _registry(root: Path):
    registry = PlaylistRegistry(root / "playlist_registry.json")
    registry.update("test-playlist", {"spotify_playlist_id": "playlist-id", "spotify_url": "https://open.spotify.com/playlist/playlist-id", "owner_id": "owner-1", "locked_uris": []})
    return registry


def test_calculate_diff_counts_adds_removes_moves_and_unchanged():
    diff = calculate_diff(["a", "b", "c", "old"], ["b", "c", "a", "new"])
    assert diff.additions == ["new"]
    assert diff.removals == ["old"]
    assert diff.moves == 1
    assert diff.unchanged == 2


def test_dry_run_does_not_modify_spotify_or_create_backup(temp_root):
    spotify = FakeSpotify([_track("spotify:track:old")])
    result = sync_playlist(
        _config(),
        spotify,
        registry=_registry(temp_root),
        mode="exact",
        dry_run=True,
        owner_id="owner-1",
        backup_root=temp_root / "backups",
        unresolved_path=temp_root / "unresolved.json",
        history_path=temp_root / "history.json",
    )
    assert result["dry_run"] is True
    assert spotify.mutations == []
    assert not (temp_root / "backups").exists()


def test_exact_sync_backups_then_replaces_items(temp_root):
    spotify = FakeSpotify([_track("spotify:track:old")])
    registry = _registry(temp_root)
    result = sync_playlist(
        _config(),
        spotify,
        registry=registry,
        mode="exact",
        yes=True,
        owner_id="owner-1",
        backup_root=temp_root / "backups",
        unresolved_path=temp_root / "unresolved.json",
        history_path=temp_root / "history.json",
    )
    assert spotify.mutations[0] == ("replace", [])
    assert result["backup"].is_file()
    backup = JsonStore(result["backup"]).read({})
    assert backup["playlist_id"] == "playlist-id"
    assert backup["snapshot_id"] == "snapshot-before"
    assert backup["tracks"][0]["spotify_uri"] == "spotify:track:old"


def test_append_mode_never_removes_or_reorders_existing_items(temp_root):
    spotify = FakeSpotify([_track("spotify:track:old")])
    sync_playlist(
        _config(),
        spotify,
        registry=_registry(temp_root),
        mode="append",
        yes=True,
        owner_id="owner-1",
        unresolved_path=temp_root / "unresolved.json",
        history_path=temp_root / "history.json",
    )
    assert spotify.mutations == []


def test_exact_mode_bypasses_backup_and_writes_when_already_correct(temp_root):
    spotify = FakeSpotify([])
    result = sync_playlist(
        _config(),
        spotify,
        registry=_registry(temp_root),
        mode="exact",
        yes=True,
        owner_id="owner-1",
        backup_root=temp_root / "backups",
        unresolved_path=temp_root / "unresolved.json",
        history_path=temp_root / "history.json",
    )
    assert spotify.mutations == []
    assert result.get("backup") is None


def test_exact_sync_batches_more_than_100_ordered_uris(temp_root):
    uris = [f"spotify:track:track{i}" for i in range(205)]
    config = PlaylistConfig(
        slug="test-playlist",
        name="Test Playlist",
        description="Description",
        tracks=[{"spotify_uri": uri} for uri in uris],
    )
    class BatchSpotify(FakeSpotify):
        def get_track(self, track_id):
            return {"id": track_id, "uri": f"spotify:track:{track_id}", "name": track_id, "artists": []}

    api = BatchSpotify([_track("spotify:track:old")])
    sync_playlist(
        config,
        api,
        registry=_registry(temp_root),
        mode="exact",
        yes=True,
        owner_id="owner-1",
        backup_root=temp_root / "backups",
        unresolved_path=temp_root / "unresolved.json",
        history_path=temp_root / "history.json",
    )
    assert len(api.mutations[0][1]) == 100
    assert [len(args[0]) for name, *args in api.mutations if name == "add"] == [105]


def test_exact_sync_appends_a_missing_suffix_without_replacing_existing_items(temp_root):
    class DirectSpotify(FakeSpotify):
        def get_track(self, track_id):
            return {"id": track_id, "uri": f"spotify:track:{track_id}", "name": track_id, "artists": []}

    config = PlaylistConfig(
        slug="test-playlist",
        name="Test Playlist",
        description="Description",
        tracks=[{"spotify_uri": "spotify:track:existing"}, {"spotify_uri": "spotify:track:new"}],
    )
    api = DirectSpotify([_track("spotify:track:existing")])
    sync_playlist(
        config,
        api,
        registry=_registry(temp_root),
        mode="exact",
        yes=True,
        owner_id="owner-1",
        backup_root=temp_root / "backups",
        unresolved_path=temp_root / "unresolved.json",
        history_path=temp_root / "history.json",
    )
    assert api.mutations == [("add", ["spotify:track:new"])]


def test_exact_sync_refuses_to_change_playlist_with_unresolved_configured_song(temp_root):
    class UnresolvedSpotify(FakeSpotify):
        def search_tracks(self, query, *, limit, offset):
            return []

    config = PlaylistConfig(
        slug="test-playlist",
        name="Test Playlist",
        description="Description",
        tracks=[{"artist": "Unknown Artist", "title": "Unknown Song"}],
    )
    api = UnresolvedSpotify([_track("spotify:track:old")])
    result = sync_playlist(
        config,
        api,
        registry=_registry(temp_root),
        mode="exact",
        yes=True,
        owner_id="owner-1",
        backup_root=temp_root / "backups",
        unresolved_path=temp_root / "unresolved.json",
        history_path=temp_root / "history.json",
    )
    assert result["blocked_unresolved"] is True
    assert api.mutations == []


def test_exact_sync_retains_previously_locked_items(temp_root):
    registry = _registry(temp_root)
    registry.update("test-playlist", {"locked_uris": ["spotify:track:locked"]})
    api = FakeSpotify([_track("spotify:track:locked")])
    result = sync_playlist(
        _config(),
        api,
        registry=registry,
        mode="exact",
        yes=True,
        owner_id="owner-1",
        backup_root=temp_root / "backups",
        unresolved_path=temp_root / "unresolved.json",
        history_path=temp_root / "history.json",
    )
    assert api.mutations == []
    assert registry.get("test-playlist")["locked_uris"] == ["spotify:track:locked"]


def test_restore_backup_requires_registration_and_restores_order(temp_root):
    api = FakeSpotify([])
    backup = {
        "slug": "test-playlist",
        "playlist_id": "playlist-id",
        "snapshot_id": "old-snapshot",
        "name": "Test Playlist",
        "description": "Description",
        "public": True,
        "tracks": [{"spotify_uri": "spotify:track:first"}, {"spotify_uri": "spotify:track:second"}],
    }
    result = restore_backup(
        backup,
        api,
        registry=_registry(temp_root),
        yes=True,
        owner_id="owner-1",
        backup_root=temp_root / "backups",
    )
    assert api.mutations[0] == ("replace", ["spotify:track:first", "spotify:track:second"])
    assert result["backup"].exists()
