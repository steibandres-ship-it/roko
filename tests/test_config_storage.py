import json
from pathlib import Path

import pytest
from PIL import Image

from app.auth import TokenStore, pkce_pair
from app.config import Settings, load_settings
from app.playlists import PlaylistConfigError, load_playlists, prepare_cover, read_backup
from app.storage import JsonStore, PlaylistRegistry


def test_redirect_uri_must_be_registered_explicit_ipv4_loopback():
    assert Settings(client_id="client").redirect_uri == "http://127.0.0.1:8888/callback"
    with pytest.raises(ValueError):
        Settings(client_id="client", redirect_uri="http://localhost:8888/callback")
    with pytest.raises(ValueError):
        load_settings(environ={"SPOTIFY_CLIENT_ID": "client", "SPOTIFY_REDIRECT_URI": "https://example.com/callback"})


def test_settings_can_be_loaded_from_supplied_environment():
    result = load_settings(environ={"SPOTIFY_CLIENT_ID": "client-123"})
    assert result.client_id == "client-123"


def test_pkce_pair_has_verifier_and_s256_challenge():
    import base64
    import hashlib

    verifier, challenge = pkce_pair()
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    assert 43 <= len(verifier) <= 128
    assert challenge == expected


def test_token_store_falls_back_to_private_local_file(tmp_path, monkeypatch):
    import keyring

    monkeypatch.setattr(keyring, "set_password", lambda *args: (_ for _ in ()).throw(RuntimeError("no backend")))
    store = TokenStore("client", tmp_path / "oauth.json")
    location = store.save({"access_token": "secret", "refresh_token": "also-secret"})
    assert location.endswith("oauth.json")
    assert store.load()["refresh_token"] == "also-secret"
    assert json.loads((tmp_path / "oauth.json").read_text())["access_token"] == "secret"


def test_playlist_json_load_and_validation(tmp_path):
    root = tmp_path / "playlists"
    root.mkdir()
    (root / "sample.json").write_text(
        json.dumps({"slug": "sample", "name": "Sample", "tracks": [{"spotify_uri": "spotify:track:one"}]}),
        encoding="utf-8",
    )
    result = load_playlists(root)
    assert len(result) == 1
    assert result[0].tracks[0].spotify_uri == "spotify:track:one"


def test_playlist_json_rejects_invalid_configuration(tmp_path):
    root = tmp_path / "playlists"
    root.mkdir()
    (root / "bad.json").write_text('{"slug":"bad","name":"Bad","tracks":[{"artist":"only"}]}', encoding="utf-8")
    with pytest.raises(PlaylistConfigError):
        load_playlists(root)


def test_seeded_ten_playlist_files_are_valid():
    from app.config import PLAYLISTS_DIR

    items = load_playlists(PLAYLISTS_DIR)
    assert len(items) == 10
    assert all(config.public for config in items)
    assert all(len(config.description) < 300 for config in items)
    assert all(len(config.tracks) == config.target_tracks for config in items)
    # Malianteo preserves one pre-existing Spotify item that was not in the original local catalog.
    assert next(config for config in items if config.slug == "malianteo-worldwide").target_tracks == 101
    assert all(config.target_tracks in {100, 101} for config in items)
    assert all(len({track.spotify_uri for track in config.tracks}) == len(config.tracks) for config in items)
    unverified = [
        (config.slug, track)
        for config in items
        for track in config.tracks
        if not track.spotify_catalog_verified
    ]
    assert len(unverified) == 1
    assert unverified[0][0] == "malianteo-worldwide"
    assert unverified[0][1].locked


def test_jpeg_cover_validation_and_size_preparation(tmp_path):
    path = tmp_path / "cover.jpg"
    Image.new("RGB", (640, 640), color=(12, 80, 150)).save(path, format="JPEG")
    data = prepare_cover(path)
    assert data.startswith(b"\xff\xd8")
    assert len(data) * 4 // 3 <= 256 * 1024
    assert Image.open(path).size == (640, 640)


def test_cover_rejects_non_square_and_non_jpeg_files(tmp_path):
    rectangular = tmp_path / "rectangular.jpg"
    Image.new("RGB", (640, 480)).save(rectangular, format="JPEG")
    with pytest.raises(ValueError, match="square"):
        prepare_cover(rectangular)
    disguised = tmp_path / "not-real.jpg"
    Image.new("RGB", (640, 640)).save(disguised, format="PNG")
    with pytest.raises(ValueError, match="not a JPEG"):
        prepare_cover(disguised)


def test_registry_writes_ids_by_slug(tmp_path):
    registry = PlaylistRegistry(tmp_path / "registry.json")
    registry.update("slug", {"spotify_playlist_id": "id-1"})
    assert registry.get("slug")["spotify_playlist_id"] == "id-1"


def test_backup_loader_only_accepts_files_inside_backup_root(tmp_path):
    root = tmp_path / "backups"
    backup = root / "backup.json"
    backup.parent.mkdir()
    JsonStore(backup).write({"slug": "slug", "playlist_id": "id", "tracks": [{"spotify_uri": "spotify:track:id"}]})
    assert read_backup(backup, backup_root=root)["slug"] == "slug"
    outside = tmp_path / "outside.json"
    outside.write_text(backup.read_text(), encoding="utf-8")
    with pytest.raises(ValueError, match="inside data/backups"):
        read_backup(outside, backup_root=root)
