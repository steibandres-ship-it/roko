from copy import deepcopy
from html import escape

import pytest

from app import editorial_expansion as expansion
from app.models import PlaylistConfig, TrackSpec
from app.storage import JsonStore, PlaylistRegistry


class FakeSpotify:
    def __init__(self):
        self.remote = {}
        self.creations = 0
        self.fail_fill = False

    def __enter__(self): return self
    def __exit__(self, *_): pass
    def get_me(self): return {"id": "owner"}
    def get_playlists(self): return [deepcopy(p) for p in self.remote.values()]
    def create_playlist(self, name, description, public):
        self.creations += 1
        item = {"id": str(self.creations), "name": name, "description": description, "public": public, "owner": {"id": "owner"}, "uris": []}
        self.remote[item["id"]] = item
        return deepcopy(item)
    def get_playlist(self, key):
        item = deepcopy(self.remote[key])
        item["description"] = escape(item["description"])
        return item
    def get_playlist_items(self, key): return [{"item": {"uri": uri}} for uri in self.remote[key]["uris"]]
    def replace_items(self, key, uris):
        if self.fail_fill: raise RuntimeError("Simulated interrupted fill")
        self.remote[key]["uris"] = list(uris)
    def update_playlist(self, key, **fields): self.remote[key].update(fields)


@pytest.fixture
def setup_publisher(tmp_path, monkeypatch):
    fake = FakeSpotify()
    monkeypatch.setattr(expansion, "ROOT", tmp_path / "data")
    monkeypatch.setattr(expansion, "STATIC", tmp_path / "static")
    monkeypatch.setattr(expansion, "PLAYLISTS_DIR", tmp_path / "playlists")
    monkeypatch.setattr(expansion, "load_settings", lambda: None)
    monkeypatch.setattr(expansion, "SpotifyClient", lambda *a, **kw: fake)
    monkeypatch.setattr(expansion, "PlaylistRegistry", lambda: PlaylistRegistry(tmp_path / "registry.json"))
    monkeypatch.setattr(expansion.time, "sleep", lambda _: None)
    config = PlaylistConfig(slug="wm-example-es", name="Ejemplo [ES]", description="R&B, d'autres artistes", tracks=[TrackSpec(spotify_uri=f"spotify:track:{i:022d}") for i in range(30)])
    plan = {"owner_id": "owner", "items": [{"theme": "example", "metadata_language": "es", "config": config.model_dump(mode="json")}]}
    return fake, plan, tmp_path


def test_interrupted_fill_stays_private_and_resumes_without_duplicate(setup_publisher):
    fake, plan, root = setup_publisher
    fake.fail_fill = True
    with pytest.raises(RuntimeError): expansion.publish(plan, 1)
    assert fake.creations == 1 and fake.remote["1"]["public"] is False
    fake.fail_fill = False
    result = expansion.publish(plan, 1)
    assert fake.creations == 1 and fake.remote["1"]["public"] is True
    assert len(fake.remote["1"]["uris"]) == 30
    assert result["wm-example-es"]["status"] == "PUBLISHED"
    expansion.publish(plan, 1)
    assert fake.creations == 1


def test_existing_identical_playlist_is_never_adopted_on_repeated_attempt(setup_publisher):
    fake, plan, root = setup_publisher
    c = plan["items"][0]["config"]
    fake.create_playlist(c["name"], c["description"], True)
    for _ in range(2):
        with pytest.raises(ValueError, match="reconciliation"):
            expansion.publish(plan, 1)
    assert fake.creations == 1 and fake.remote["1"]["uris"] == []


def test_user_edits_to_pending_playlist_are_preserved(setup_publisher):
    fake, plan, root = setup_publisher
    fake.fail_fill = True
    with pytest.raises(RuntimeError): expansion.publish(plan, 1)
    fake.remote["1"]["uris"] = ["spotify:track:USERADDED00000000000000"]
    fake.fail_fill = False
    with pytest.raises(ValueError, match="protect edits"):
        expansion.publish(plan, 1)
    assert fake.remote["1"]["uris"] == ["spotify:track:USERADDED00000000000000"]
    assert fake.remote["1"]["public"] is False


def test_remastered_song_is_deduplicated():
    base = {"title": "Trátame Suavemente", "artists": ["Soda Stereo"]}
    remaster = {**base, "title": "Trátame Suavemente - Remasterizado 2007"}
    assert expansion.song_key(base) == expansion.song_key(remaster)


def test_lost_create_response_recovers_exact_remote_without_duplicate(setup_publisher, monkeypatch):
    fake, plan, root = setup_publisher
    create = fake.create_playlist
    def lose_response(*args):
        create(*args)
        raise RuntimeError("Simulated lost response after server commit")
    monkeypatch.setattr(fake, "create_playlist", lose_response)
    with pytest.raises(RuntimeError): expansion.publish(plan, 1)
    monkeypatch.setattr(fake, "create_playlist", create)
    result = expansion.publish(plan, 1)
    assert fake.creations == 1
    assert result["wm-example-es"]["status"] == "PUBLISHED"
