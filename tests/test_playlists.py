import pytest

from app.models import PlaylistConfig
from app.playlists import create_one, recover_matches, store_recovered_playlist
from app.storage import PlaylistRegistry


class CreateAPI:
    def __init__(self, playlists=None):
        self.playlists = playlists or []
        self.created = 0

    def get_playlists(self):
        return self.playlists

    def create_playlist(self, name, description, public):
        self.created += 1
        created = {
            "id": "spotify-id",
            "name": name,
            "owner": {"id": "owner"},
            "external_urls": {"spotify": "https://open.spotify.com/playlist/spotify-id"},
        }
        self.playlists.append(created)
        return created

    def get_playlist(self, playlist_id):
        raise AssertionError("registered ID is trusted; playlist creation should not look it up by name")


def config():
    return PlaylistConfig(slug="sample", name="Sample", description="Description", tracks=[])


def test_playlist_creation_is_idempotent_and_uses_registry_id(tmp_path):
    api = CreateAPI()
    registry = PlaylistRegistry(tmp_path / "registry.json")
    url, created = create_one(api, config(), registry, "owner", known_playlists=[])
    assert created
    assert url == "https://open.spotify.com/playlist/spotify-id"
    url_again, created_again = create_one(api, config(), registry, "owner", known_playlists=[])
    assert not created_again
    assert url_again == url
    assert api.created == 1


def test_creation_refuses_to_duplicate_unregistered_same_name(tmp_path):
    existing = {"id": "existing-id", "name": "Sample", "owner": {"id": "owner"}}
    api = CreateAPI([existing])
    with pytest.raises(ValueError, match="playlists recover sample"):
        create_one(api, config(), PlaylistRegistry(tmp_path / "registry.json"), "owner")
    assert api.created == 0


def test_recovery_only_matches_owned_playlists_and_records_explicit_id(tmp_path):
    api = CreateAPI(
        [
            {"id": "owned", "name": "Sample", "owner": {"id": "owner"}},
            {"id": "followed", "name": "Sample", "owner": {"id": "someone-else"}},
        ]
    )
    matches = recover_matches(api, [config()], owner_id="owner")
    assert [item["id"] for item in matches["sample"]] == ["owned"]
    registry = PlaylistRegistry(tmp_path / "registry.json")
    store_recovered_playlist(config(), matches["sample"][0], registry, "owner")
    assert registry.get("sample")["spotify_playlist_id"] == "owned"
