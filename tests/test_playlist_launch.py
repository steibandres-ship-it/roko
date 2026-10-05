from __future__ import annotations

import json

import pytest

from app.storage import PlaylistRegistry
from app.world_music import playlist_launch
from app.world_music.playlist_blueprints import PLAYLIST_BLUEPRINTS


def _ready_plan() -> dict:
    blueprint = PLAYLIST_BLUEPRINTS[0]
    candidates = [
        {
            "spotify_uri": f"spotify:track:{index:022d}",
            "title": f"Song {index}",
            "artist": f"Artist {index}",
            "proposed_priority": "growth",
        }
        for index in range(30)
    ]
    return {
        "slug": blueprint["slug"],
        "status": "READY_TO_LAUNCH",
        "config": playlist_launch.config_from_blueprint(blueprint),
        "candidates": candidates,
    }


class FakeSpotify:
    def __init__(self, *, wrong_identity: bool = False):
        self.wrong_identity = wrong_identity
        self.calls: list[str] = []
        self.items: list[str] = []
        self.public = False

    def get_track(self, track_id: str) -> dict:
        index = int(track_id)
        return {"id": track_id, "name": "Wrong song" if self.wrong_identity else f"Song {index}", "artists": [{"name": f"Artist {index}"}]}

    def create_playlist(self, name: str, description: str, public: bool) -> dict:
        assert public is False
        self.calls.append("create_private")
        return {"id": "spotify-list-id", "name": name, "owner": {"id": "me"}, "external_urls": {"spotify": "https://open.spotify.com/playlist/spotify-list-id"}}

    def get_playlist(self, playlist_id: str) -> dict:
        return {"id": playlist_id, "owner": {"id": "me"}, "public": self.public}

    def get_playlist_items(self, playlist_id: str) -> list[dict]:
        return [{"item": {"uri": uri}} for uri in self.items]

    def replace_items(self, playlist_id: str, uris: list[str]) -> None:
        self.calls.append("seed_items")
        self.items = list(uris)

    def update_playlist(self, playlist_id: str, *, name: str, description: str, public: bool) -> None:
        self.calls.append("publish")
        self.public = public


def test_all_twenty_blueprints_form_valid_playlist_configs():
    configs = [playlist_launch.config_from_blueprint(blueprint) for blueprint in PLAYLIST_BLUEPRINTS]
    assert len(configs) == len({config.slug for config in configs}) == 20
    assert all(config.target_tracks >= playlist_launch.MIN_SEED_TRACKS for config in configs)


def test_launch_seeds_privately_then_publishes(monkeypatch, tmp_path):
    registry = PlaylistRegistry(tmp_path / "registry.json")
    monkeypatch.setattr(playlist_launch, "PlaylistRegistry", lambda: registry)
    monkeypatch.setattr(playlist_launch, "PLAYLISTS_DIR", tmp_path)
    plan = _ready_plan()
    spotify = FakeSpotify()

    result = playlist_launch.publish_ready_blueprint(plan, spotify, owner_id="me", known_playlists=[], spotify_track_cache={})

    assert result["status"] == "PUBLISHED"
    assert result["tracks"] == 30
    assert spotify.calls == ["create_private", "seed_items", "publish"]
    assert registry.get(plan["slug"])["launch_state"] == "ACTIVE"
    saved = json.loads((tmp_path / f"{plan['slug']}.json").read_text(encoding="utf-8"))
    assert saved["public"] is True
    assert len(saved["tracks"]) == 30


def test_identity_mismatch_blocks_spotify_creation(monkeypatch, tmp_path):
    registry = PlaylistRegistry(tmp_path / "registry.json")
    monkeypatch.setattr(playlist_launch, "PlaylistRegistry", lambda: registry)
    monkeypatch.setattr(playlist_launch, "PLAYLISTS_DIR", tmp_path)
    plan = _ready_plan()
    spotify = FakeSpotify(wrong_identity=True)

    with pytest.raises(ValueError, match="catalog identity"):
        playlist_launch.publish_ready_blueprint(plan, spotify, owner_id="me", known_playlists=[], spotify_track_cache={})

    assert spotify.calls == []
    assert registry.get(plan["slug"]) is None
